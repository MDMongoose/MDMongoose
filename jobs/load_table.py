"""mdm_load: refresh Tables' datasets in the load pipeline, then merge them into MDMNodes.

--table_uid   one or more Table UIDs (comma-separated), all, or due (every Table whose Schedule (cron, UTC)
              fired in the last hour)
--step        which part of the job this task does (mdm.load_steps):
                prepare  resolve the Tables, wait for an earlier run that loads one of them, one pipeline
                         update for all of them; the list goes to the next tasks as task values
                table    merge one Table (an iteration of the load_tables For each task)
                after    matching and golden records for Tables with Run Golden After Load, then the exports
                all      everything in this one task, one Table after another (mdm_init)
--run_pipeline  'true' starts a pipeline update first: Incremental tables with refresh_selection
                (the change data feed of the staging table is applied), Full reload tables with
                full_refresh_selection (rec_<key> is rebuilt from the staging table)
Every Table is merged set-based (mdm.load_spark): only the rec_<key> changes since the last merged version are
read, and one MERGE writes them into the Table's branch of MDMNodes. Every table load, match and golden refresh
writes a row to mdm_core.mdm_log.
"""

import json

from _common import log_sink, repo_for, setup

args, spark = setup("table_uid", "user", "run_pipeline", "pipeline_id", "export_job_id", "exports", "step", "job_id",
                    table_uid="all", user="mdm_load", run_pipeline="true", step="all")
from mdm import load_spark, load_steps, runlog  # noqa: E402
from mdm.load import LOAD_INCREMENTAL, dataset_key  # noqa: E402

repo = repo_for(spark, args.catalog)
sink = log_sink(repo)
user = args.user or "mdm_load"
step = (args.step or "all").lower()


def run_pipeline(tables):
    if args.run_pipeline.lower() != "true" or not args.pipeline_id or not tables:
        return
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    inc = [f"rec_{dataset_key(t)}" for t in tables if (t["properties"] or {}).get("Load Mode") == LOAD_INCREMENTAL]
    full = [f"rec_{dataset_key(t)}" for t in tables if f"rec_{dataset_key(t)}" not in inc]
    from mdm.pipeline_runs import run_update
    print(f"load pipeline: incremental {inc}, full {full}")
    # one update for all the Tables; it waits behind an update that is already running instead of failing
    state = run_update(args.pipeline_id, w, refresh_selection=inc or None, full_refresh_selection=full or None)
    if state != "COMPLETED":
        raise SystemExit(f"load pipeline update ended {state}")


def load_one(t):
    src = load_spark.rec_table_name(args.catalog, t)
    if not spark.catalog.tableExists(src):
        print(f"skip {t['name']}: {src} does not exist yet (run the load pipeline)")
        return
    with runlog.activity(sink, "load", t["uid"], (t["properties"] or {}).get("Load Mode"), user, args.job_run_id) as lg:
        res = load_spark.run(spark, repo, repo.table, t, src, user)
        lg.stats.update(res)
        lg.row["mode"] = res.get("mode")
    print(res)


def after(tables):
    """Matching, then golden records, of the Master Data nodes that use the loaded tables; then the exports."""
    from mdm.catalog import Catalog
    from mdm.matching import after_load
    wanted = {t["uid"] for t in tables if (t["properties"] or {}).get("Run Golden After Load")}
    if wanted:
        after_load.run(repo, Catalog(repo), wanted, user, args.job_run_id)
    if args.export_job_id and (args.exports or "all").strip().lower() != "none":   # none: a Golden records run exports at its end
        from databricks.sdk import WorkspaceClient
        try:  # views, files and materialized views of the Export nodes (queued if a refresh is running)
            run = WorkspaceClient().jobs.run_now(job_id=int(args.export_job_id), job_parameters={"uid": "all"})
            print(f"mdm_export started (run {run.run_id})")
        except Exception as e:  # noqa: BLE001
            print(f"mdm_export not started: {e}")


if step == "prepare":
    tables = load_steps.resolve_tables(repo, args.table_uid)
    uids = [t["uid"] for t in tables]
    print(f"tables: {', '.join(t['name'] + ' ' + t['uid'] for t in tables) or 'none'}")
    load_steps.wait_for_same_tables(args.job_id, args.job_run_id, uids)
    run_pipeline(tables)
    load_steps.set_task_values({"tables": json.dumps(uids), "tables_csv": ",".join(uids) or "none",
                                "count": len(uids)})
elif step == "table":
    for t in load_steps.resolve_tables(repo, args.table_uid):
        load_one(t)
elif step == "after":
    after(load_steps.resolve_tables(repo, args.table_uid))
else:   # all, in this one task
    tables = load_steps.resolve_tables(repo, args.table_uid)
    if not tables:
        print("no table to load")
        raise SystemExit(0)
    run_pipeline(tables)
    for t in tables:
        load_one(t)
    after(tables)
