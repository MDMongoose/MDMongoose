"""mdm_match_train: train the Splink model of a Match Rule (Exact keys + Splink).

--uid       the Match Rule (or a Master Data node: its Match Rule)
--activate  true: the new model is used right away; false (default): saved as a draft to compare
            in a full preview and activate in the Match Rule editor (the first model is always active)
Logged in mdm_core.mdm_log (activity match_train): version, records, accuracy against steward decisions.
"""

import json

from _common import log_sink, repo_for, setup

args, spark = setup("uid", "user", "activate", user="mdm_match_train", activate="false")
from mdm import runlog  # noqa: E402
from mdm.matching import run as mr  # noqa: E402
from mdm.model import K  # noqa: E402

repo = repo_for(spark, args.catalog)
sink = log_sink(repo)
node = repo.get(args.uid.strip().upper())
if not node:
    raise SystemExit(f"{args.uid} not found")
rule = (node["properties"] or {}).get("Match Rule") if K.NT_MASTER_DATA in node["types"] else node["uid"]
with runlog.activity(sink, "match_train", rule, "train", args.user, args.job_run_id) as lg:
    res = mr.train(repo, rule, args.user, activate=args.activate.lower() == "true")
    lg.stats.update(res)
print(json.dumps(res))
