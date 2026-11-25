"""Processing of a Master Data node's golden records run: One Machine or Distributed.

The Master Data node's Processing property (Enum ProcessingModes) picks it for the whole run — matching and
survivorship. Both give the same result:
    One Machine   all records on one machine (DuckDB for cleaning and comparisons). Fastest on small and
                  medium tables, no cluster to start; runs out of memory on very large ones.
    Distributed   the same steps spread over a Spark cluster (mdm.distributed). Handles any size; starting the
                  cluster work costs a little time on small tables.
Exact keys + Splink always runs on One Machine processing. App previews (cleaning, a rule on two records,
survivorship of one entity) always run on One Machine processing; a Preview run of matching follows the setting.
Distributed processing needs Spark (the jobs); where there is none (local mode, the app) the run falls back to
One Machine processing and says so.
"""

from __future__ import annotations

from .model import K
from .uid import to_uid

ONE_MACHINE, DISTRIBUTED = to_uid(1491), to_uid(1492)   # Enum ProcessingModes
LABELS = {"one_machine": "One Machine", "distributed": "Distributed"}
SPLINK_NOTE = "Exact keys + Splink runs on One Machine processing"


def match_method(repo, master: dict) -> str | None:
    from .matching.spec import METHODS
    rule = repo.get((master.get("properties") or {}).get("Match Rule") or "")
    if not rule:
        return None
    return METHODS.get((rule.get("properties") or {}).get("Method"), "fuzzy")


def chosen(master: dict) -> str:
    """The setting on the node: one_machine (also when empty) or distributed."""
    return "distributed" if (master.get("properties") or {}).get("Processing") == DISTRIBUTED else "one_machine"


def mode_of(repo, master: dict) -> str:
    """The processing a run of this Master Data node uses."""
    if chosen(master) == "distributed" and match_method(repo, master) != "splink":
        return "distributed"
    return "one_machine"


def spark_of(repo):
    """The Spark session behind a repository (the jobs), or None (the app's SQL warehouse, local mode)."""
    x = getattr(repo, "x", None)
    for _ in range(3):
        if x is None:
            return None
        if getattr(x, "spark", None) is not None:
            return x.spark
        x = getattr(x, "inner", None)
    return None


def _io(repo, user: str):
    spark = spark_of(repo)
    if spark is None:
        return None
    from .distributed.io import SparkNodes
    return SparkNodes(spark, repo.table, repo, user)


def _note(stats: dict, mode: str, wanted: str, why: str | None = None) -> None:
    stats["processing"] = LABELS[mode]
    if wanted != mode and why:
        stats["processing_note"] = why


def run_match(repo, master: dict, rule_uid: str, mode: str = "incremental", preview: bool = False,
              user: str = "mdm_match") -> dict:
    """Matching of the Master Data node's Match Rule with the node's processing."""
    from .matching import run as mr
    wanted, use = chosen(master), mode_of(repo, master)
    why = SPLINK_NOTE if wanted != use else None
    io = _io(repo, user) if use == "distributed" else None
    if use == "distributed" and io is None:
        use, why = "one_machine", "Distributed processing runs in the jobs; this run used One Machine processing"
    if use == "distributed":
        from .distributed import run_match as dist
        res = dist(io, rule_uid, mode=mode, preview=preview, user=user)
    else:
        res = mr.run(repo, rule_uid, mode=mode, preview=preview, user=user)
    _note(res["stats"], use, wanted, why)
    return res


def refresh_golden(repo, catalog, master_uid: str, user: str = "mdm_golden", retire: bool = True) -> dict:
    """Refresh Golden Records of a Master Data node with the node's processing."""
    from . import golden
    master = repo.get(master_uid)
    if master is None or K.NT_MASTER_DATA not in master["types"]:
        return golden.refresh(repo, catalog, master_uid, user, retire=retire)
    wanted, use = chosen(master), mode_of(repo, master)
    why = SPLINK_NOTE if wanted != use else None
    io = _io(repo, user) if use == "distributed" else None
    if use == "distributed" and io is None:
        use, why = "one_machine", "Distributed processing runs in the jobs; this run used One Machine processing"
    if use == "distributed":
        from .distributed import refresh_golden as dist
        res = dist(io, catalog, master_uid, user, retire=retire)
    else:
        res = golden.refresh(repo, catalog, master_uid, user, retire=retire)
    _note(res, use, wanted, why)
    return res
