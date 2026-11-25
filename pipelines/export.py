"""Export pipeline: MDMNodes -> consumer-facing materialized views + history.

  nodes            MV, live nodes (incremental refresh: row tracking + CDF on MDMNodes)
  node_index       MV, uid → path_key / name, clustered by uid: the app's lookup and search index
  node_edges       MV, ancestor/descendant pairs from the path array (no recursion)
  node_history     streaming table, SCD2 history of every node from MDMNodes' change feed
  <Object Name>    MV per Export node with Export Type = Materialized view (Model / Exports):
                   the exploded branch, L0_UID, L0_Name, L1_… — the same table as the Export dialog.
                   The SQL joins MDMNodes on uid / parent only, so Databricks can refresh it
                   incrementally (row tracking is on for MDMNodes; see the event log's
                   planning_information to check how each refresh ran).
Views and CSV / Excel exports are written by jobs/run_exports.py, before this pipeline runs.
"""

import sys

from pyspark import pipelines as dp
from pyspark.sql import functions as F

NODES_TABLE = spark.conf.get("mdm.nodes_table")  # noqa: F821
sys.path.insert(0, spark.conf.get("mdm.src_path"))  # noqa: F821


@dp.materialized_view(name="nodes", comment="Live MDM nodes (one row per node)")
def nodes():
    return (spark.read.table(NODES_TABLE).where("NOT is_deleted")  # noqa: F821
            .drop("is_deleted", "record_hash"))


@dp.materialized_view(name="node_index", cluster_by=["uid"],
                      comment="uid → path_key / name lookup for the app (clustered by uid); MDMNodes stays the source of truth")
def node_index():
    return (spark.read.table(NODES_TABLE).where("NOT is_deleted")  # noqa: F821
            .select("uid", "uid_num", "name", "path_key", "parent_uid", "depth", "origin", "types"))


@dp.materialized_view(name="node_edges", comment="Every ancestor -> descendant pair with its distance")
def node_edges():
    n = spark.read.table(NODES_TABLE).where("NOT is_deleted")  # noqa: F821
    return (n.select("uid", "depth", F.posexplode("path").alias("pos", "ancestor_uid"))
            .select("ancestor_uid", F.col("uid").alias("descendant_uid"), (F.col("depth") - F.col("pos")).alias("distance")))


def _export_views():
    """(name, comment, sql) of every Export node of type Materialized view."""
    from mdm import exports
    from mdm.catalog import Catalog
    from mdm.sqlrepo import SparkExecutor, SqlRepo
    repo = SqlRepo(SparkExecutor(spark), NODES_TABLE)  # noqa: F821
    cat = Catalog(repo).refresh(force=True)
    name_of = cat.namer()
    out = []
    for node in exports.export_nodes(repo):
        spec = exports.spec_of(node, name_of)
        if spec["kind"] != "mv" or not exports.NAME_RE.match(spec["name"]) or spec["name"] in exports.RESERVED:
            continue
        try:
            sql, _ = exports.build_sql(repo, cat, spec, NODES_TABLE)
        except Exception as e:  # noqa: BLE001 - one broken export must not stop the others
            print(f"export {node['uid']} skipped: {e}")
            continue
        out.append((spec["name"], f"MDMongoose export {node['uid']} — {node['name']}", sql))
    return out


def declare_export(name, comment, sql):
    @dp.materialized_view(name=name, comment=comment)
    def exported():
        return spark.sql(sql)  # noqa: F821


for _x in _export_views():
    declare_export(*_x)


@dp.temporary_view(name="node_changes")
def node_changes():
    return (spark.readStream.option("readChangeFeed", "true").table(NODES_TABLE)  # noqa: F821
            .where("_change_type IN ('insert', 'update_postimage')")
            .withColumn("properties", F.to_json("properties"))
            .drop("_change_type", "_commit_timestamp"))


dp.create_streaming_table(name="node_history", comment="SCD2 history of every node (from MDMNodes change data feed)")
dp.create_auto_cdc_flow(
    target="node_history", source="node_changes", keys=["uid"], sequence_by=F.col("_commit_version"),
    stored_as_scd_type=2, except_column_list=["_commit_version"])
