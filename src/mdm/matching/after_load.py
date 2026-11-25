"""After a load: incremental matching, then golden records, for every Master Data node that uses the tables.

Tables with "Run Golden After Load" trigger it (mdm_load job, local mode). Each activity gets its own
mdm_log row. A Master Data node without a Match Rule only refreshes its golden records. Each Master Data
node runs with its own Processing (One Machine or Distributed, see mdm.processing).
"""

from __future__ import annotations

from ..model import K


def affected_masters(repo, table_uids: set[str]) -> list[tuple[dict, str | None]]:
    out = []
    for m in repo.by_types([K.NT_MASTER_DATA]):
        p = m["properties"] or {}
        mrule = repo.get(p.get("Match Rule") or "")
        srule = repo.get(p.get("Survivorship Rule") or "")
        srcs = set(((mrule or {}).get("properties") or {}).get("Sources") or []) | \
            set(((srule or {}).get("properties") or {}).get("Sources") or [])
        if srcs & table_uids:
            out.append((m, mrule["uid"] if mrule else None))
    return out


def run(repo, catalog, table_uids: set[str], user: str, job_run_id: str | None = None) -> list[dict]:
    from .. import processing, runlog
    sink = runlog.sink_for(repo)
    out = []
    for master, rule_uid in affected_masters(repo, set(table_uids)):
        res = {"master": master["name"]}
        try:
            if rule_uid:
                with runlog.activity(sink, "match", rule_uid, "incremental", user, job_run_id) as lg:
                    m = processing.run_match(repo, master, rule_uid, mode="incremental", user=user)
                    lg.stats.update(m["stats"], rows=m["rows"][:100])
                res["match"] = {k: m["stats"].get(k) for k in ("changed", "affected", "new_entities", "merged", "split",
                                                                "pairs_new")}
            if (master["properties"] or {}).get("Survivorship Rule"):
                with runlog.activity(sink, "golden", master["uid"], "refresh", user, job_run_id) as lg:
                    g = processing.refresh_golden(repo, catalog, master["uid"], user)
                    lg.stats.update(g)
                res["golden"] = g
        except Exception as e:  # noqa: BLE001 - one master's problem does not stop the others
            res["error"] = str(e)
        out.append(res)
        print(res)
    return out
