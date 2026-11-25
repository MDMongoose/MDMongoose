"""The steps of the mdm_load job, which loads several Tables at the same time.

    prepare      which Tables (a list of UIDs, all, or due); waits while an earlier active mdm_load run loads
                 one of them; one load-pipeline update for all of them; hands the list to the next tasks
    load_tables  a Databricks For each task: one iteration per Table (up to 4 at a time) merges its
                 rec_<key> changes into the Table's branch of MDMNodes
    after_load   matching and golden records for the Tables with Run Golden After Load, then the exports

Different Tables load in parallel; the same Table never loads twice at the same time (a later run waits).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from .model import K

EVERYTHING = {"", "ALL", "DUE"}   # a run with one of these may load any Table


def parse_uids(value: str | None) -> list[str]:
    """'MDM-1A, MDM-1B' -> ['MDM-1A', 'MDM-1B']; all / due / '' -> [] (decided by resolve_tables)."""
    v = (value or "").strip()
    if v.upper() in EVERYTHING or v.lower() == "none":
        return []
    return [u.strip().upper() for u in v.split(",") if u.strip()]


def resolve_tables(repo, value: str | None, now: datetime | None = None) -> list[dict]:
    """The Table nodes of a table_uid parameter: UIDs (comma-separated), all, or due (Schedule fired in the
    last hour). Only Tables with a Table Name; an unknown UID or a node that is not a Table is an error."""
    v = (value or "").strip()
    if v.lower() == "none":
        return []
    if v.upper() == "DUE":
        from croniter import croniter
        now = (now or datetime.now(timezone.utc)).replace(second=0, microsecond=0)

        def due(t):
            cron = (t["properties"] or {}).get("Schedule")
            if not cron:
                return False
            try:
                prev = croniter(cron, now).get_prev(datetime)
            except Exception:  # noqa: BLE001 - invalid cron: skip, the UI validates
                return False
            return now - prev < timedelta(hours=1)
        tables = [t for t in repo.by_types([K.NT_TABLE]) if due(t)]
    elif v.upper() in EVERYTHING:
        tables = repo.by_types([K.NT_TABLE])
    else:
        tables = []
        for u in parse_uids(v):
            t = repo.get(u)
            if not t or K.NT_TABLE not in t["types"]:
                raise SystemExit(f"{u} is not a Table node")
            tables.append(t)
    return [t for t in tables if (t["properties"] or {}).get("Table Name")]


def overlaps(mine: list[str], theirs_param: str | None) -> bool:
    """Whether another run's table_uid parameter may load one of my Tables."""
    if (theirs_param or "").strip().upper() in EVERYTHING:
        return True
    return bool(set(mine) & set(parse_uids(theirs_param)))


def wait_for_same_tables(job_id: str, run_id: str, mine: list[str], poll: int = 15, max_wait: int = 3 * 3600,
                         client=None) -> None:
    """Runs of mdm_load start in parallel; one that would load a Table an earlier active run loads waits."""
    if not (job_id and run_id and mine):
        return
    if client is None:
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient()
    me = int(run_id)
    deadline = time.time() + max_wait
    while True:
        busy = []
        for r in client.jobs.list_runs(job_id=int(job_id), active_only=True):
            if not r.run_id or r.run_id >= me:   # only runs that started before this one
                continue
            p = {x.name: (x.value if x.value is not None else x.default) for x in (r.job_parameters or [])}
            if overlaps(mine, p.get("table_uid")):
                busy.append(r.run_id)
        if not busy:
            return
        if time.time() > deadline:
            raise SystemExit(f"run(s) {busy} loading the same table(s) are still running")
        print(f"waiting for run(s) {busy} that load the same table(s)")
        time.sleep(poll)


def set_task_values(values: dict) -> None:
    """Hand values to the next tasks of the job ({{tasks.prepare.values.<key>}}); printed outside Databricks."""
    try:
        from databricks.sdk.runtime import dbutils
        for k, v in values.items():
            dbutils.jobs.taskValues.set(key=k, value=v)
    except Exception as e:  # noqa: BLE001 - not in a Databricks job
        print(f"task values (not set: {e}): {values}")
