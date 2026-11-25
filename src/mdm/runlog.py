"""mdm_core.mdm_log — one row per run of every job activity (load, match, match_train, golden, export, init).

A row is inserted when the activity starts (status RUNNING) and updated when it ends (SUCCESS / FAILED),
so Monitoring sees running work too. `stats` is a VARIANT with per-activity numbers (for matching:
records, pairs, new / merged / split entities, pairs to review, model version).

    with runlog.activity(sink, "match", node_uid=rule_uid, mode="incremental", triggered_by=user,
                         job_run_id=args.job_run_id) as run:
        run.stats.update(records=1200, pairs=4100)

Sinks: SqlLogSink (a SqlExecutor: Spark in jobs, the SQL warehouse in the app) and MemoryLogSink
(local mode and tests). Logging never breaks the job: a sink error is printed and swallowed.
"""

from __future__ import annotations

import json
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

ACTIVITIES = ("init", "load", "match", "match_train", "golden", "export")

DDL = """CREATE TABLE IF NOT EXISTS {table} (
  run_id STRING NOT NULL COMMENT 'one id per activity run',
  job_run_id STRING COMMENT 'Databricks job run id (empty when run by the app)',
  activity STRING NOT NULL COMMENT 'init | load | match | match_train | golden | export',
  node_uid STRING COMMENT 'Table, Match Rule, Master Data or Export node the activity ran on',
  mode STRING COMMENT 'full | incremental | preview | ...',
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  status STRING COMMENT 'RUNNING | SUCCESS | FAILED',
  triggered_by STRING,
  stats VARIANT COMMENT 'per-activity numbers',
  message STRING
) USING DELTA
CLUSTER BY (node_uid, started_at)
COMMENT 'MDMongoose run history: one row per job activity'"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class MemoryLogSink:
    def __init__(self):
        self.rows: list[dict] = []

    def ensure(self):
        return None

    def start(self, row: dict) -> None:
        self.rows.append(dict(row))

    def finish(self, row: dict) -> None:
        for r in self.rows:
            if r["run_id"] == row["run_id"]:
                r.update(row)
                return
        self.rows.append(dict(row))

    def read(self, node_uid: str | None = None, activity: str | None = None, job_run_id: str | None = None,
             limit: int = 50) -> list[dict]:
        out = [r for r in self.rows if (not node_uid or r.get("node_uid") == node_uid)
               and (not activity or r.get("activity") == activity)
               and (not job_run_id or str(r.get("job_run_id")) == str(job_run_id))]
        return [dict(r) for r in sorted(out, key=lambda r: r.get("started_at") or "", reverse=True)[:limit]]


class SqlLogSink:
    def __init__(self, executor, table: str):
        self.x, self.table = executor, table

    def ensure(self):
        self.x.execute(DDL.format(table=self.table))

    def start(self, row: dict) -> None:
        self.x.execute(
            f"INSERT INTO {self.table} (run_id, job_run_id, activity, node_uid, mode, started_at, status, "
            "triggered_by, stats, message) VALUES (:run_id, :job_run_id, :activity, :node_uid, :mode, "
            "CAST(:started_at AS TIMESTAMP), 'RUNNING', :triggered_by, parse_json(:stats), NULL)",
            {k: row.get(k) for k in ("run_id", "job_run_id", "activity", "node_uid", "mode", "started_at",
                                     "triggered_by")} | {"stats": json.dumps(row.get("stats") or {}, default=str)})

    def finish(self, row: dict) -> None:
        self.x.execute(
            f"UPDATE {self.table} SET finished_at = CAST(:finished_at AS TIMESTAMP), status = :status, mode = :mode, "
            "stats = parse_json(:stats), message = :message WHERE run_id = :run_id",
            {"run_id": row["run_id"], "finished_at": row["finished_at"], "status": row["status"], "mode": row.get("mode"),
             "stats": json.dumps(row.get("stats") or {}, default=str), "message": (row.get("message") or "")[:4000]})

    def read(self, node_uid: str | None = None, activity: str | None = None, job_run_id: str | None = None,
             limit: int = 50) -> list[dict]:
        where, params = [], {}
        if node_uid:
            where.append("node_uid = :node_uid")
            params["node_uid"] = node_uid
        if activity:
            where.append("activity = :activity")
            params["activity"] = activity
        if job_run_id:
            where.append("job_run_id = :job_run_id")
            params["job_run_id"] = str(job_run_id)
        sql = (f"SELECT run_id, job_run_id, activity, node_uid, mode, CAST(started_at AS STRING) AS started_at, "
               f"CAST(finished_at AS STRING) AS finished_at, status, triggered_by, to_json(stats) AS stats, message "
               f"FROM {self.table}" + (" WHERE " + " AND ".join(where) if where else "")
               + f" ORDER BY started_at DESC LIMIT {int(limit)}")
        rows = self.x.query_fresh(sql, params) if hasattr(self.x, "query_fresh") else self.x.query(sql, params)
        for r in rows:
            try:
                r["stats"] = json.loads(r["stats"]) if isinstance(r.get("stats"), str) else (r.get("stats") or {})
            except ValueError:
                r["stats"] = {}
        return rows


def table_of(nodes_table: str) -> str:
    """mdm_log lives next to MDMNodes, in the core schema."""
    return nodes_table.rsplit(".", 1)[0] + ".mdm_log"


_memory = MemoryLogSink()


def sink_for(repo) -> Any:
    """The log of a repository: SqlLogSink for SqlRepo (Spark or warehouse), else the process-wide memory log."""
    x = getattr(repo, "x", None)
    table = getattr(repo, "table", None)
    if x is not None and table:
        return SqlLogSink(getattr(x, "inner", x), table_of(table))
    return _memory


class Run:
    def __init__(self, sink, activity: str, node_uid: str | None, mode: str | None, triggered_by: str | None,
                 job_run_id: str | None):
        if activity not in ACTIVITIES:
            raise ValueError(f"unknown activity {activity}")
        self.sink = sink
        self.row = {"run_id": uuid.uuid4().hex, "job_run_id": str(job_run_id) if job_run_id else None,
                    "activity": activity, "node_uid": node_uid, "mode": mode, "started_at": _now(),
                    "status": "RUNNING", "triggered_by": triggered_by, "stats": {}, "message": None}
        self.stats: dict[str, Any] = self.row["stats"]
        self.message: str | None = None
        self._t0 = time.monotonic()
        self._safe(sink.start, self.row)

    @staticmethod
    def _safe(fn, row):
        try:
            fn(row)
        except Exception as e:  # noqa: BLE001 - logging never breaks the job
            print(f"mdm_log: could not write ({e})")

    def finish(self, status: str, message: str | None = None) -> dict:
        self.stats.setdefault("seconds", round(time.monotonic() - self._t0, 1))
        self.row.update(status=status, finished_at=_now(), message=message or self.message)
        self._safe(self.sink.finish, self.row)
        return self.row


@contextmanager
def activity(sink, activity_name: str, node_uid: str | None = None, mode: str | None = None,
             triggered_by: str | None = None, job_run_id: str | None = None):
    run = Run(sink, activity_name, node_uid, mode, triggered_by, job_run_id)
    try:
        yield run
    except BaseException as e:
        run.finish("FAILED", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"[:4000])
        raise
    else:
        run.finish("SUCCESS")
