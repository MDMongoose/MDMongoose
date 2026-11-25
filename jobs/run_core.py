"""mdm_core: matching, golden records and background actions — one job, several runs at the same time.

--action    match                  match the source records of Master Data nodes, then (golden=true) refresh their
                                   golden records — the Match step of the Golden records run, with Survivorship
            mdm.golden.refresh     refresh the golden records of Master Data nodes (Survivorship on its own)
            mdm.export.run         write one export file into the exports volume
            mdm_custom.<fn>        any function fn(repo, catalog, uid, user) of the mdm_custom package
--uid       the node(s), comma-separated; for match also "all" (every Master Data node with a Match Rule)
--mode      match: incremental (default: only changed records and the entities they touch) | full
--preview   match: true = compute and log what would change, write nothing (the result is in mdm_log)
--golden    match: true (default) = refresh the golden records after an applied run
--exports   none | all | UID[,UID]: afterwards, in this same task, write those file exports (CSV / Excel) and
            refresh the materialized views (an update of the export pipeline, waited for)

Matching and golden records use the Master Data node's Processing (mdm.processing): One Machine, or Distributed
on this job's Spark compute. Runs on different Master Data nodes run in parallel (their nodes are disjoint; Delta write conflicts on the
shared table are retried). A run on a node that another active run of this job is working on waits for it.
Each activity writes a row to mdm_core.mdm_log.
"""

import importlib
import json
import time

from _common import log_sink, repo_for, setup

args, spark = setup("action", "uid", "user", "format", "depth", "balanced", "options", "mode", "preview", "golden",
                    "exports", "export_pipeline_id", "job_id",
                    action="mdm.golden.refresh", user="mdm_core", format="xlsx", depth="1", balanced="false",
                    options="{}", mode="incremental", preview="false", golden="true", exports="none",
                    export_pipeline_id="", job_id="")
from mdm import names, processing, runlog  # noqa: E402
from mdm.catalog import Catalog  # noqa: E402
from mdm.export_refresh import parse_uids, refresh_files, refresh_views  # noqa: E402
from mdm.model import K  # noqa: E402

UIDS = [u.strip().upper() for u in args.uid.split(",") if u.strip()]
ALL = not UIDS or UIDS == ["ALL"]


def wait_for_same_nodes(poll: int = 15, max_wait: int = 3 * 3600) -> None:
    """Runs of this job start in parallel; one on the same node(s) as an earlier active run waits for it."""
    if not (args.job_id and args.job_run_id):
        return
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    me = int(args.job_run_id)
    deadline = time.time() + max_wait
    while True:
        busy = []
        for r in w.jobs.list_runs(job_id=int(args.job_id), active_only=True):
            if not r.run_id or r.run_id >= me:   # only runs that started before this one
                continue
            p = {x.name: (x.value if x.value is not None else x.default) for x in (r.job_parameters or [])}
            theirs = [u.strip().upper() for u in (p.get("uid") or "").split(",") if u.strip()]
            if ALL or not theirs or "ALL" in theirs or set(theirs) & set(UIDS):
                busy.append(r.run_id)
        if not busy:
            return
        if time.time() > deadline:
            raise SystemExit(f"run(s) {busy} on the same node(s) are still running")
        print(f"waiting for run(s) {busy} on the same node(s)")
        time.sleep(poll)


wait_for_same_nodes()
repo = repo_for(spark, args.catalog)
sink = log_sink(repo)
failed: list[str] = []
changed = True   # whether the exports have something new

if args.action == "match":
    from mdm.matching import run as mr
    preview = args.preview.lower() == "true"
    changed = not preview
    if ALL:
        masters = [m for m in repo.by_types([K.NT_MASTER_DATA]) if (m["properties"] or {}).get("Match Rule")]
    else:
        masters = [repo.get(u) for u in UIDS]
    for m in masters:
        if not m:
            continue
        rule = (m["properties"] or {}).get("Match Rule") if K.NT_MASTER_DATA in m["types"] else m["uid"]
        if not rule:
            print(f"{m['name']}: no Match Rule — skipped")
            continue
        try:
            with runlog.activity(sink, "match", rule, "preview" if preview else args.mode, args.user, args.job_run_id) as lg:
                if K.NT_MASTER_DATA in m["types"]:   # the node's Processing: One Machine or Distributed
                    res = processing.run_match(repo, m, rule, mode=args.mode, preview=preview, user=args.user)
                else:
                    res = mr.run(repo, rule, mode=args.mode, preview=preview, user=args.user)
                lg.stats.update(res["stats"], rows=res["rows"][:100], master=m["uid"])
            print(json.dumps(res["stats"]))
            if not preview and args.golden.lower() == "true" and K.NT_MASTER_DATA in m["types"] \
                    and (m["properties"] or {}).get("Survivorship Rule"):
                with runlog.activity(sink, "golden", m["uid"], "refresh", args.user, args.job_run_id) as lg:
                    g = processing.refresh_golden(repo, Catalog(repo), m["uid"], args.user)
                    lg.stats.update(g)
                print(json.dumps(g))
        except Exception as e:  # noqa: BLE001 - the next Master Data node still runs; the job fails at the end
            failed.append(f"{m['name']}: {e}")
            print(f"FAILED {m['name']}: {e}")
    changed = changed and len(failed) < len(masters)

elif args.action == "mdm.golden.refresh":
    opts = json.loads(args.options or "{}")
    catalog = Catalog(repo)
    for uid in UIDS:
        if repo.get(uid) is None:
            if args.user == "mdm_init":  # demo golden refresh during init, demo not seeded
                print(f"{uid} not found — skipped")
                continue
            raise SystemExit(f"{uid} not found")
        with runlog.activity(sink, "golden", uid, "refresh", args.user, args.job_run_id) as lg:
            res = processing.refresh_golden(repo, catalog, uid, args.user, retire=opts.get("retire", True) is not False)
            lg.stats.update(res)
        print(json.dumps(res))

else:
    uid = UIDS[0] if UIDS else ""
    if repo.get(uid) is None:
        raise SystemExit(f"{uid} not found")
    catalog = Catalog(repo)
    if args.action == "mdm.export.run":
        from mdm import export
        node = repo.get(uid)
        name, data, _ = export.run(repo, catalog.refresh(), node, args.format, int(args.depth), args.balanced == "true")
        path = f"{names.volume_path(args.catalog)}/{uid}/{name}"
        from databricks.sdk import WorkspaceClient
        import io
        WorkspaceClient().files.upload(path, io.BytesIO(data), overwrite=True)
        print(f"wrote {path}")
    elif args.action.startswith("mdm_custom."):
        mod, _, fn = args.action.rpartition(".")
        print(getattr(importlib.import_module(mod), fn)(repo, catalog, uid, args.user))
    else:
        raise SystemExit(f"unknown action handler {args.action}")

wanted = parse_uids(args.exports)
if changed and wanted != []:   # the exports in the same task: no second compute start-up
    res, bad = refresh_files(repo, args.catalog, wanted, args.user, args.job_run_id, sink)
    print(json.dumps(res, indent=1))
    if bad:
        print(f"WARNING: {bad} export(s) failed — see Last Run on the Export nodes")
    # the materialized views: an update of the export pipeline, waited for (it waits behind a running update)
    if refresh_views(args.export_pipeline_id) not in (None, "COMPLETED"):
        failed.append("the mdm_export pipeline update did not complete — see the pipeline's update history")
if failed:
    raise SystemExit("; ".join(failed))
