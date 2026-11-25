"""Runtime wiring: repository, current user, background runs."""

from __future__ import annotations

import os
import threading
import time

from mdm import names
from mdm.catalog import Catalog
from mdm.permissions import User
from mdm.repo import MemoryRepo, NodeRepo

from ..service import MdmService

LOCAL = os.getenv("MDM_LOCAL") == "1"


def _names_from_jobs() -> None:
    """The bundle target and its catalog, schema and volume names: read from the arguments of the mdm_core
    job (set from the databricks.yml variables), so app.yaml needs no per-target values."""
    jid = os.getenv("MDM_CORE_JOB_ID") or os.getenv("MDM_ACTION_JOB_ID")
    if LOCAL or not jid:
        return
    try:
        from databricks.sdk import WorkspaceClient
        job = WorkspaceClient().jobs.get(int(jid))
        params = next(t.spark_python_task.parameters for t in job.settings.tasks if t.spark_python_task)
        kv = dict(zip(params[0::2], params[1::2]))
        names.configure(kv.get("--catalog"), kv.get("--core_schema"), kv.get("--staging_schema"),
                        kv.get("--export_schema"), kv.get("--export_volume"))
        names.TARGET = kv.get("--target") or names.TARGET
    except Exception as e:  # noqa: BLE001 - fall back to the environment / defaults
        print(f"schema names not read from the mdm_core job ({e}); using MDM_* environment variables or defaults")


_names_from_jobs()
TABLE = os.getenv("MDM_NODES_TABLE", "")  # optional override of <catalog>.<core schema>.mdmnodes
if not TABLE or TABLE.startswith("__"):
    TABLE = names.nodes_table()
names.configure(catalog=TABLE.split(".")[0], core=TABLE.split(".")[1])
ENVIRONMENT = "local" if LOCAL else (names.TARGET or "")  # the bundle target, shown in the logo tooltip
ADMIN_GROUPS = {"admins"}
ADMINS = {x.strip().lower() for x in os.getenv("MDM_ADMINS", "").split(",") if x.strip()}


def _workspace_client():
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()


def build_service() -> MdmService:
    if LOCAL:
        from mdm.sources import DemoSourceReader
        return MdmService(_local_repo(), sources=DemoSourceReader())
    from mdm.sources import SqlSourceReader
    from mdm.sqlrepo import SqlRepo, WarehouseExecutor
    wid = os.environ["DATABRICKS_WAREHOUSE_ID"]
    from mdm.sqlrepo import CachingExecutor
    executor = WarehouseExecutor(_workspace_client(), wid)
    catalog = TABLE.split(".")[0]
    index = os.getenv("MDM_INDEX_TABLE") or f"{catalog}.{names.EXPORT}.node_index"
    secs = float(os.getenv("MDM_CACHE_SECONDS", "10"))
    reads = CachingExecutor(executor, TABLE, (index,), secs, background=True) if secs > 0 else executor
    repo: NodeRepo = SqlRepo(reads, TABLE, index)
    return MdmService(repo, Catalog(repo, background=True), sources=SqlSourceReader(executor))


def _local_repo() -> MemoryRepo:
    """MDM_LOCAL=1: in-memory hierarchy with the demo seeded and loaded."""
    import hashlib
    import json

    from mdm import load
    from mdm.model import K
    from mdm.seed import DEMO_SOURCE_ROWS, DEMO_TABLES, all_seed
    from mdm.uid import to_uid

    repo = MemoryRepo(all_seed("workspace", names.STAGING, demo=True))
    for table_uid, src in DEMO_TABLES.items():
        spec = DEMO_SOURCE_ROWS[src]
        rows = []
        for r in spec["rows"]:
            d = dict(zip(spec["columns"], r))
            d["_record_hash"] = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
            rows.append(d)
        load.merge_records(repo, repo.get(table_uid), rows)
    if os.getenv("MDM_LOCAL_MATCH", "1") != "0":
        from mdm.catalog import Catalog
        from mdm.matching import after_load
        after_load.run(repo, Catalog(repo), set(DEMO_TABLES), "mdm_init")
    bulk = int(os.getenv("MDM_LOCAL_BULK", "0") or 0)
    if bulk:  # a big level to try paging ("Load next 40", server filters) locally
        from mdm.model import new_node
        ref = repo.get(to_uid(1400))
        nums = iter(repo.allocate(bulk + 1))
        folder = new_node(next(nums), f"Bulk demo ({bulk})", [K.NT_FOLDER], ref, icon="box")
        kids = [new_node(next(nums), f"Item {i:05d}", [K.NT_FOLDER], folder, icon="flag" if i % 3 == 0 else "box")
                for i in range(bulk)]
        repo.upsert([folder] + kids, "local")
    assert repo.get(K.ROOT)
    return repo


# ----------------------------------------------------------------------------- users
_group_cache: dict[str, tuple[float, set[str]]] = {}


def _groups_for(email: str, token: str | None) -> set[str]:
    hit = _group_cache.get(email)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    groups: set[str] = set()
    try:
        if token:  # on-behalf-of-user token (when user authorization is enabled for the app)
            from databricks.sdk import WorkspaceClient
            me = WorkspaceClient(host=os.environ.get("DATABRICKS_HOST"), token=token, auth_type="pat").current_user.me()
        else:  # the app's service principal looks the user up
            users = list(_workspace_client().users.list(filter=f'userName eq "{email}"', attributes="groups"))
            me = users[0] if users else None
        if me and me.groups:
            groups = {g.display for g in me.groups if g.display}
    except Exception:  # noqa: BLE001 - lookup is best effort, "users" still applies
        pass
    groups.add("users")
    _group_cache[email] = (time.time(), groups)
    return groups


def current_user(headers) -> User:
    if LOCAL:
        email = headers.get("x-mdm-local-user") or os.getenv("MDM_LOCAL_USER") or "local.admin@example.com"
        groups = {"users", "admins"} if "admin" in email else {"users"}
        return User(email, groups, is_admin="admins" in groups)
    email = headers.get("x-forwarded-email") or headers.get("x-forwarded-preferred-username") or "unknown"
    groups = _groups_for(email, headers.get("x-forwarded-access-token"))
    admin = bool(groups & ADMIN_GROUPS) or email.lower() in ADMINS
    return User(email, groups, is_admin=admin)


# ----------------------------------------------------------------------------- background runs
class Runner:
    """Starts job/pipeline actions. On Databricks: Jobs API run_now; locally: in-process thread."""

    def __init__(self, service: MdmService):
        self.service = service
        self.local_runs: dict[str, dict] = {}

    def start(self, handler: str, uid: str, user: str, options: dict | None = None) -> dict:
        if LOCAL:
            return self._local(handler, uid, user, options or {})
        w = _workspace_client()
        o = options or {}
        # exports: none | all | Export node UIDs — the file exports run in the job's own task, then its
        # export_pipeline task refreshes the materialized views (one compute start-up less than mdm_export)
        exports = o.get("exports")
        if isinstance(exports, list):
            exports = ",".join(exports) or "none"
        if handler.startswith("pipeline:"):
            job_id = int(os.environ["MDM_LOAD_JOB_ID"])
            params = {"table_uid": uid, "user": user, "exports": exports or "all"}
        elif handler == "job:mdm_export":
            job_id = int(os.environ["MDM_EXPORT_JOB_ID"])
            params = {"uid": uid, "user": user}
        elif handler == "job:mdm_match":   # matching runs in mdm_core (action = match)
            job_id = int(os.environ["MDM_CORE_JOB_ID"])
            params = {"action": "match", "uid": uid, "user": user, "mode": o.get("mode") or "incremental",
                      "preview": str(bool(o.get("preview"))).lower(), "golden": str(o.get("golden", True) is not False).lower(),
                      "exports": "none" if o.get("preview") else (exports or "all")}
        elif handler == "job:mdm_match_train":
            job_id = int(os.environ["MDM_MATCH_TRAIN_JOB_ID"])
            params = {"uid": uid, "user": user, "activate": str(bool((options or {}).get("activate"))).lower()}
        else:
            job_id = int(os.environ["MDM_CORE_JOB_ID"])
            import json
            params = {"action": handler, "uid": uid, "user": user, "exports": exports or "all",
                      "options": json.dumps({k: v for k, v in o.items() if k != "exports"})}
        run = w.jobs.run_now(job_id=job_id, job_parameters=params)
        return {"run_id": str(run.run_id), "state": "QUEUED"}

    def status(self, run_id: str) -> dict:
        if LOCAL or run_id.startswith(("local-", "flow-")):  # flow-: a chained run of the app (golden_run)
            return self.local_runs.get(run_id, {"state": "UNKNOWN"})
        w = _workspace_client()
        r = w.jobs.get_run(int(run_id))
        life = r.state.life_cycle_state.value if r.state and r.state.life_cycle_state else "UNKNOWN"
        result = r.state.result_state.value if r.state and r.state.result_state else None
        state = {"TERMINATED": "SUCCESS" if result == "SUCCESS" else "FAILED"}.get(life, life)
        if life in ("INTERNAL_ERROR", "SKIPPED"):
            state = "FAILED"
        out = {"run_id": run_id, "state": state, "message": r.state.state_message if r.state else None,
               "url": r.run_page_url}
        if state in ("SUCCESS", "FAILED"):  # what the job logged (matching: counts and the entity changes)
            from mdm import runlog
            try:
                out["log"] = runlog.sink_for(self.service.repo).read(job_run_id=run_id, limit=20)
            except Exception as e:  # noqa: BLE001 - mdm_log not there yet
                out["log_error"] = str(e)
        return out

    def _local(self, handler: str, uid: str, user: str, options: dict) -> dict:
        from mdm import golden, load
        from mdm.seed import DEMO_SOURCE_ROWS

        run_id = f"local-{len(self.local_runs) + 1}"
        self.local_runs[run_id] = {"run_id": run_id, "state": "RUNNING"}

        def work():
            from mdm import runlog
            sink = runlog.sink_for(self.service.repo)
            try:
                repo = self.service.repo
                if handler == "job:mdm_match":
                    from mdm.matching import run as mr
                    from mdm.model import K
                    node = repo.get(uid)
                    rule = (node["properties"] or {}).get("Match Rule") if K.NT_MASTER_DATA in node["types"] else uid
                    preview = bool(options.get("preview"))
                    mode = options.get("mode") or "incremental"
                    from mdm import processing
                    with runlog.activity(sink, "match", rule, "preview" if preview else mode, user, run_id) as lg:
                        if K.NT_MASTER_DATA in node["types"]:   # local mode has no Spark: One Machine processing
                            res = processing.run_match(repo, node, rule, mode=mode, preview=preview, user=user)
                        else:
                            res = mr.run(repo, rule, mode=mode, preview=preview, user=user)
                        lg.stats.update(res["stats"], rows=res["rows"][:100])
                    out = {"match": res["stats"], "rows": res["rows"][:100]}
                    if not preview and options.get("golden", True) is not False and K.NT_MASTER_DATA in node["types"]:
                        with runlog.activity(sink, "golden", uid, "refresh", user, run_id) as lg:
                            out["golden"] = processing.refresh_golden(repo, self.service.catalog, uid, user)
                            lg.stats.update(out["golden"])
                    self.local_runs[run_id] = {"run_id": run_id, "state": "SUCCESS", "message": "matching done",
                                               "log": sink.read(job_run_id=run_id)}
                    return
                if handler == "job:mdm_match_train":
                    from mdm.matching import run as mr
                    with runlog.activity(sink, "match_train", uid, "train", user, run_id) as lg:
                        res = mr.train(repo, uid, user, activate=bool(options.get("activate")))
                        lg.stats.update(res)
                    self.local_runs[run_id] = {"run_id": run_id, "state": "SUCCESS", "message": f"model {res['version']} trained",
                                               "log": sink.read(job_run_id=run_id)}
                    return
                if handler == "mdm.golden.refresh":
                    from mdm import processing
                    with runlog.activity(sink, "golden", uid, "refresh", user, run_id) as lg:
                        out = processing.refresh_golden(repo, self.service.catalog, uid, user,
                                                        retire=options.get("retire", True) is not False)
                        lg.stats.update(out)
                elif handler == "job:mdm_export":
                    from mdm import export, exports
                    node = repo.get(uid)
                    spec = exports.spec_of(node, self.service.catalog.namer())
                    root = repo.get(spec["root"] or "")
                    rows = len(export.build(repo, self.service.catalog, root, exports.options(spec))[2]) if root else 0
                    out = f"local mode: {rows} rows would be written to {exports.location(spec, 'workspace')}"
                elif handler.startswith("pipeline:"):   # one or more Tables (comma-separated), like mdm_load
                    import hashlib
                    import json
                    from mdm.load_steps import parse_uids
                    out, after = {}, set()
                    for u in parse_uids(uid):
                        node = repo.get(u)
                        src = (node["properties"].get("Table Name") or "").split(".")[-1]
                        spec = DEMO_SOURCE_ROWS.get(src)
                        if not spec:
                            raise RuntimeError(f"local mode only has demo sources, not {src}")
                        rows = []
                        for r in spec["rows"]:
                            d = dict(zip(spec["columns"], r))
                            d["_record_hash"] = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
                            rows.append(d)
                        with runlog.activity(sink, "load", u, "full", user, run_id) as lg:
                            out[node["name"]] = load.merge_records(repo, node, rows, user)
                            lg.stats.update(out[node["name"]])
                        if (node["properties"] or {}).get("Run Golden After Load"):
                            after.add(u)
                    if after:
                        from mdm.matching import after_load
                        out = {"load": out, "after": after_load.run(repo, self.service.catalog, after, user, run_id)}
                else:
                    raise RuntimeError(f"unknown handler {handler}")
                time.sleep(0.6)
                self.local_runs[run_id] = {"run_id": run_id, "state": "SUCCESS", "message": str(out)}
            except Exception as e:  # noqa: BLE001
                self.local_runs[run_id] = {"run_id": run_id, "state": "FAILED", "message": str(e),
                                           "log": sink.read(job_run_id=run_id)}

        threading.Thread(target=work, daemon=True).start()
        return {"run_id": run_id, "state": "RUNNING"}
