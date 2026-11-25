"""mdm_export (first task): refresh the Export nodes that are not materialized views.

  CSV / Excel  the file /Volumes/<catalog>/mdm_export/exports/<Object Name>.csv|.xlsx

Materialized views are declared in the mdm_export pipeline; this task then starts its update and waits for it
(behind an update that is already running, instead of failing).
Views are created by the app when the node is saved or Run Export is clicked (a view always shows
current data; the app's service principal owns it), so they are skipped here.
--uid all refreshes every Export node; a UID (or a comma-separated list) only those.
Each node gets "Last Run" (time, rows / columns, or the error) so the app shows the result.
"""

import json

from _common import log_sink, repo_for, setup

args, spark = setup("uid", "user", "export_pipeline_id", uid="all", user="mdm_export", export_pipeline_id="")
from mdm.export_refresh import parse_uids, refresh_files, refresh_views  # noqa: E402

repo = repo_for(spark, args.catalog)
results, failed = refresh_files(repo, args.catalog, parse_uids(args.uid), args.user, args.job_run_id, log_sink(repo))
print(json.dumps(results, indent=1))
# then the materialized views, in this same task: an update of the export pipeline that waits behind a running one
state = refresh_views(args.export_pipeline_id)
if state not in (None, "COMPLETED"):
    raise SystemExit(f"the mdm_export pipeline update ended {state}")
if failed:
    raise SystemExit(f"{failed} export(s) failed — see Last Run on the Export nodes")
