"""Metadata-driven load pipeline (Spark Declarative Pipelines), fed by change data feed.

Sources are Delta tables in mdm_staging with change data feed enabled; an upstream
process lands the source system's data there. At every update the pipeline reads the
Table nodes from MDMNodes and declares, per table:
  changes_<key>  streaming view: the staging table's change feed, trimmed, expectations
  rec_<key>      streaming table: AUTO CDC (SCD1) of those changes — the cleaned current
                 state, itself with change data feed on
A new Table node in the UI becomes new datasets on the next update; "Load Childs" refreshes
only rec_<key> (refresh_selection), so loads are partial. jobs/load_table.py then merges the
change feed of rec_<key> into MDMNodes, set-based.
"""

import sys

from pyspark import pipelines as dp
from pyspark.sql import functions as F

NODES_TABLE = spark.conf.get("mdm.nodes_table")  # noqa: F821 - `spark` is provided by the pipeline
sys.path.insert(0, spark.conf.get("mdm.src_path"))  # noqa: F821

from mdm.load import table_config  # noqa: E402
from mdm.model import K  # noqa: E402


def _table_nodes():
    rows = (spark.read.table(NODES_TABLE)  # noqa: F821
            .where(f"NOT is_deleted AND array_contains(types, '{K.NT_TABLE}')")
            .select("uid", "uid_num", "name", F.to_json("properties").alias("p"))
            .collect())
    import json
    out = []
    for r in rows:
        cfg = table_config({"uid": r.uid, "uid_num": r.uid_num, "name": r.name, "properties": json.loads(r.p or "{}")})
        if cfg["source"] and cfg["keys"]:
            out.append(cfg)
    return out


TABLES = _table_nodes()


@dp.materialized_view(name="load_tables", comment="Table nodes the load pipeline was generated from")
def load_tables():
    schema = "uid STRING, dataset STRING, source STRING, keys ARRAY<STRING>, expectations ARRAY<STRING>"
    return spark.createDataFrame(  # noqa: F821
        [(t["uid"], f"rec_{t['key']}", t["source"], t["keys"], t["expectations"]) for t in TABLES], schema)


DATA_SKIP = {"_change_type", "_commit_version", "_commit_timestamp"}


def declare(cfg):
    key = cfg["key"]
    expectations = {f"rule_{i + 1}": e for i, e in enumerate(cfg["expectations"])}

    @dp.temporary_view(name=f"changes_{key}", comment=f"change feed of {cfg['source']}")
    @dp.expect_all_or_drop(expectations)
    def changes():
        df = (spark.readStream.option("readChangeFeed", "true").table(cfg["source"])  # noqa: F821
              .where("_change_type IN ('insert', 'update_postimage', 'delete')"))
        cols = sorted(c for c in df.columns if c not in DATA_SKIP)
        return df.select(
            *[F.nullif(F.trim(F.col(f"`{c}`").cast("string")), F.lit("")).alias(c) for c in cols],
            F.col("_change_type"), F.col("_commit_version"), F.col("_commit_timestamp").alias("_changed_at"))

    dp.create_streaming_table(
        name=f"rec_{key}",
        comment=f"current records of {cfg['source']} for Table node {cfg['uid']}",
        table_properties={"delta.enableChangeDataFeed": "true", "mdm.table_uid": cfg["uid"]},
    )
    dp.create_auto_cdc_flow(
        target=f"rec_{key}", source=f"changes_{key}", keys=cfg["keys"],
        sequence_by=F.col("_commit_version"),
        apply_as_deletes=F.expr("_change_type = 'delete'"),
        except_column_list=["_change_type", "_commit_version"],
        stored_as_scd_type=1)


for _cfg in TABLES:
    declare(_cfg)
