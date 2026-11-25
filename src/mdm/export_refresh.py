"""Refresh the Export nodes that are files (CSV / Excel) and record "Last Run" on every Export node.

Used by the mdm_export job and, in the same task, by the job that changes data (mdm_core), so a
golden-record refresh and its exports need one compute start-up instead of two. Materialized views are refreshed
by the mdm_export pipeline (the jobs' export_pipeline task); Views are created by the app and are always current.
"""

from __future__ import annotations

import io

from . import export, exports, runlog
from .catalog import Catalog
from .model import now_iso


def refresh_views(pipeline_id: str | None, log=print) -> str | None:
    """Refresh the materialized views: an update of the mdm_export pipeline, started in the calling task and
    waited for. It queues behind an update that is already running (see pipeline_runs). Returns its state."""
    if not pipeline_id:
        return None
    from .pipeline_runs import run_update
    state = run_update(pipeline_id, log=log)
    log(f"mdm_export pipeline: {state}")
    return state


def parse_uids(value: str | None) -> list[str] | None:
    """'all' / '' -> None (every Export node); 'none' -> []; else the comma-separated UIDs."""
    v = (value or "").strip()
    if v.lower() in ("", "all"):
        return None
    if v.lower() == "none":
        return []
    return [u.strip().upper() for u in v.split(",") if u.strip()]


def refresh_files(repo, catalog_name: str, uids: list[str] | None, user: str, job_run_id: str = "",
                  sink=None) -> tuple[list[dict], int]:
    """Writes the CSV / Excel files of the Export nodes (uids None = all) and their Last Run.
    Returns (results, number failed)."""
    if uids == []:
        return [], 0
    cat = Catalog(repo).refresh(force=True)
    name_of = cat.namer()
    sink = sink or runlog.sink_for(repo)
    results, failed = [], 0
    for node in exports.export_nodes(repo, uids):
        spec = exports.spec_of(node, name_of)
        where = exports.location(spec, catalog_name) if spec["kind"] else "?"
        try:
            if spec["kind"] is None or not exports.NAME_RE.match(spec["name"]):
                raise ValueError("Export Type or Object Name is not valid — open the node and save it again")
            if spec["kind"] == "mv":
                status = f"{now_iso()} · refreshed by the mdm_export pipeline"
            elif spec["kind"] == "view":
                continue  # created by the app; always current
            else:
                root = repo.get(spec["root"])
                if not root:
                    raise ValueError("the Root Node does not exist")
                name, data, _ = export.run(repo, cat, root, opt=exports.options(spec))
                from databricks.sdk import WorkspaceClient
                WorkspaceClient().files.upload(where, io.BytesIO(data), overwrite=True)
                groups, cols, rows = export.build(repo, cat, root, exports.options(spec))
                status = f"{now_iso()} · {len(rows)} rows written · {len(data):,} bytes"
        except Exception as e:  # noqa: BLE001 - report per node, keep going
            failed += 1
            status = f"{now_iso()} · FAILED: {e}"
        results.append({"uid": node["uid"], "name": node["name"], "location": where, "status": status})
        r = runlog.Run(sink, "export", node["uid"], spec["kind"], user, job_run_id)
        r.stats.update(location=where)
        r.finish("FAILED" if "FAILED" in status else "SUCCESS", status)
        fresh = repo.get(node["uid"]) or node
        repo.upsert([dict(fresh, properties={**(fresh["properties"] or {}), "Last Run": status, "Location": where})],
                    user or "mdm_export")
    return results, failed
