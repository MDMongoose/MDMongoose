"""Set-based load of a Table's records into MDMNodes (Spark), driven by change data feed.

    staging table (CDF on, filled upstream)
      → load pipeline: changes_<key> → AUTO CDC → rec_<key> (CDF on)
      → this module: rec_<key> change feed since the last merged version
                     → one MERGE into MDMNodes (only the Table's branch is read: path_key prefix)

Incremental mode reads the change feed of rec_<key> from the version after the last one merged
(kept on the Table node as `_cdf_version`). Full mode compares the whole rec_<key> table with
the Table's records (first load, after a pipeline full refresh, or Load Mode = Full reload).
UserDefined values are never overwritten. Nothing is collected to the driver except counts.
Runs on serverless: no cache()/persist(), no RDDs; inputs are read at pinned versions instead.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from .load import DELETE_HARD, DELETE_KEEP, LOAD_INCREMENTAL, dataset_key, table_config
from .matching.spec import MATCH_PROPS
from .model import K, path_key

SQL_TRANSFORM = {"": "{c}", "—": "{c}", "none": "{c}", "trim()": "trim({c})", "upper()": "upper({c})",
                 "lower()": "lower({c})", "upper(trim())": "upper(trim({c}))", "title()": "initcap({c})"}
META = {"_change_type", "_commit_version", "_commit_timestamp", "_changed_at"}


def _q(c: str) -> str:
    return "`" + c.replace("`", "``") + "`"


def prepare_changes(df: DataFrame, cfg: dict, incremental: bool) -> DataFrame:
    """rec_<key> rows (or its change feed) → one row per source key:
    source_key, _op ('upsert' | 'delete'), name, props (MAP<STRING,STRING>), record_hash."""
    data_cols = [c for c in df.columns if c not in META]
    if incremental:
        w = Window.partitionBy(*[F.col(_q(k)) for k in cfg["keys"]]).orderBy(F.col("_commit_version").desc(),
                                                                          F.col("_change_type").desc())
        df = (df.where("_change_type IN ('insert', 'update_postimage', 'delete')")
              .withColumn("_rn", F.row_number().over(w)).where("_rn = 1").drop("_rn")
              .withColumn("_op", F.when(F.col("_change_type") == "delete", "delete").otherwise("upsert")))
    else:
        df = df.withColumn("_op", F.lit("upsert"))
    key = F.concat_ws("|", *[F.coalesce(F.col(_q(k)).cast("string"), F.lit("")) for k in cfg["keys"]])
    if cfg["mapping"]:
        pairs = []
        for src, prop, tr in cfg["mapping"]:
            if src not in data_cols:
                continue
            pairs += [F.lit(prop), F.expr(SQL_TRANSFORM.get((tr or "").lower(), "{c}").format(c=_q(src))).cast("string")]
    else:
        pairs = [x for c in sorted(data_cols) for x in (F.lit(c), F.col(_q(c)).cast("string"))]
    props = F.map_from_arrays(F.array(*pairs[0::2]), F.array(*pairs[1::2])) if pairs else \
        F.create_map().cast("map<string,string>")
    name = F.coalesce(F.expr(cfg["name_expression"]).cast("string") if cfg["name_expression"] else F.lit(None).cast("string"),
                      key)
    return df.select(key.alias("source_key"), F.col("_op"), name.alias("name"), props.alias("props"),
                     F.sha2(F.to_json(props), 256).alias("record_hash"))


def existing_records(spark: SparkSession, nodes_table: str, table_node: dict, version: int | None = None) -> DataFrame:
    """The Table's current Record children — only the branch's files are read. `version` pins the
    snapshot, so the plan is the same each time it is evaluated (no cache() on serverless)."""
    reader = spark.read.option("versionAsOf", version) if version is not None else spark.read
    return (reader.table(nodes_table)
            .where(F.col("path_key").startswith(path_key(table_node)))
            .where(F.col("parent_uid") == table_node["uid"])
            .where("origin = 'loaded'")
            .select("uid", "uid_num", "source_key", "record_hash", "is_deleted", "locked", "name",
                    F.coalesce(F.col("user_defined"), F.create_map().cast("map<string,boolean>")).alias("ud"),
                    F.from_json(F.to_json("properties"), "map<string,string>").alias("old_props")))


def plan(staged: DataFrame, existing: DataFrame, table_node: dict, cfg: dict, full: bool) -> DataFrame:
    """Join changes to existing records → rows with `_op` in insert | update | delete_soft | delete_hard.
    New rows get `_seq` (1..n) for UID allocation; they have no uid yet."""
    s, e = staged.alias("s"), existing.alias("e")
    j = s.join(e, F.col("s.source_key") == F.col("e.source_key"), "full_outer" if full else "left")
    delete_op = ("delete_hard" if cfg["delete_mode"] == DELETE_HARD else
                 None if cfg["delete_mode"] == DELETE_KEEP else "delete_soft")
    gone = F.col("s.source_key").isNull() if full else F.lit(False)
    upsert = F.col("s._op") == "upsert"
    changed = (F.col("e.record_hash").isNull() | (F.col("e.record_hash") != F.col("s.record_hash"))
               | F.col("e.is_deleted"))
    op = (F.when(upsert & F.col("e.uid").isNull(), "insert")
          .when(upsert & changed, "update")
          .when((F.col("s._op") == "delete") | gone,
                F.when(F.col("e.uid").isNotNull() & ~F.col("e.is_deleted") & ~F.col("e.locked"), F.lit(delete_op))))
    # UserDefined values win over the source: keep the old value for every UserDefined key
    # ... and so do the properties matching wrote (Golden UID, Match Score, Matched By, ...)
    match_props = F.array(*[F.lit(x) for x in MATCH_PROPS])
    kept_old = F.map_filter(F.coalesce(F.col("e.old_props"), F.create_map().cast("map<string,string>")),
                            lambda k, v: F.coalesce(F.col("e.ud")[k], F.lit(False)) | F.array_contains(match_props, k))
    fresh = F.map_filter(F.col("s.props"), lambda k, v: ~F.coalesce(F.col("e.ud")[k], F.lit(False))
                         & ~F.array_contains(match_props, k))
    stamp = F.date_format(F.current_timestamp(), "yyyy-MM-dd'T'HH:mm:ssXXX")
    props = F.map_concat(fresh, kept_old, F.map_from_arrays(F.array(F.lit("Loaded At")), F.array(stamp)))
    name = F.when(F.coalesce(F.col("e.ud")["Name"], F.lit(False)), F.col("e.name")).otherwise(F.col("s.name"))
    out = j.select(F.col("e.uid").alias("uid"), F.coalesce(F.col("s.source_key"), F.col("e.source_key")).alias("source_key"),
                   op.alias("_op"), name.alias("name"), F.to_json(props).alias("props_json"),
                   F.col("s.record_hash").alias("record_hash")).where("_op IS NOT NULL")
    return out.withColumn("_seq", F.when(F.col("_op") == "insert", F.row_number().over(
        Window.partitionBy(F.col("_op")).orderBy("source_key"))))


def finalize(planned: DataFrame, table_node: dict, cfg: dict, first_uid: int, user: str) -> DataFrame:
    """Add every MDMNodes column; inserts get uid_num = first_uid + _seq - 1."""
    parent_path = list(table_node["path"]) + [table_node["uid"]]
    tkey = path_key(table_node)
    uid_num = F.when(F.col("_op") == "insert", F.lit(first_uid) + F.col("_seq") - 1)
    new_uid = F.concat(F.lit("MDM-"), F.upper(F.conv(uid_num.cast("string"), 10, 36)))
    uid = F.coalesce(F.col("uid"), new_uid)
    types = [t for t in [cfg["record_type"], K.NT_RECORD] if t]
    return planned.select(
        uid.alias("uid"), uid_num.cast("bigint").alias("uid_num"), "name",
        F.array(*[F.lit(t) for t in types]).alias("types"), F.lit(table_node["uid"]).alias("parent_uid"),
        F.array(*[F.lit(u) for u in parent_path]).alias("path"), F.lit(len(parent_path)).alias("depth"),
        F.lit(table_node["uid"]).alias("source_table_uid"), "source_key", "record_hash", "props_json",
        F.lit(cfg["record_icon"]).cast("string").alias("icon"),
        F.concat(F.lit(tkey), F.substring(uid, 5, 64), F.lit("/")).alias("path_key"),
        F.lit(user).alias("modified_by"), "_op")


def merge_sql(nodes_table: str, view: str, tkey: str) -> str:
    return f"""MERGE INTO {nodes_table} AS t
USING {view} AS s
ON t.uid = s.uid AND t.path_key LIKE '{tkey}%'
WHEN MATCHED AND s._op = 'delete_hard' THEN DELETE
WHEN MATCHED AND s._op = 'delete_soft' THEN UPDATE SET
  t.is_deleted = true, t.modified_at = current_timestamp(), t.modified_by = s.modified_by
WHEN MATCHED AND s._op = 'update' THEN UPDATE SET
  t.name = s.name, t.properties = parse_json(s.props_json), t.record_hash = s.record_hash, t.is_deleted = false,
  t.modified_at = current_timestamp(), t.modified_by = s.modified_by
WHEN NOT MATCHED AND s._op = 'insert' THEN INSERT
  (uid, uid_num, name, types, parent_uid, path, depth, sort_order, child_links, origin, source_table_uid, source_key,
   record_hash, properties, user_defined, icon, max_child_depth, allowed_child_types, owner, steward, locked,
   is_deleted, modified_at, modified_by, path_key, kind)
  VALUES (s.uid, s.uid_num, s.name, s.types, s.parent_uid, s.path, s.depth, NULL, array(), 'loaded',
   s.source_table_uid, s.source_key, s.record_hash, parse_json(s.props_json), map(), s.icon, 0, array(), NULL,
   array(), false, false, current_timestamp(), s.modified_by, s.path_key, NULL)"""


def rec_table_name(catalog: str, table_node: dict) -> str:
    from . import names
    return f"{catalog}.{names.STAGING}.rec_{dataset_key(table_node)}"


def latest_version(spark: SparkSession, table: str) -> int:
    return int(spark.sql(f"DESCRIBE HISTORY {table} LIMIT 1").collect()[0]["version"])


def _retry_conflicts(fn, attempts: int = 6):
    """Run a write again when Delta reports a conflict with a write that ran at the same time (another Table's
    MERGE into MDMNodes): each Table writes only its own branch, so running it again is safe."""
    import random
    import time
    from .sqlrepo import is_concurrency_conflict
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if attempt == attempts - 1 or not is_concurrency_conflict(e):
                raise
            time.sleep(0.5 * 2 ** attempt + random.random())


def run(spark: SparkSession, repo, nodes_table: str, table_node: dict, rec_table: str, user: str,
        force_full: bool = False) -> dict:
    cfg = table_config(table_node)
    props = table_node["properties"] or {}
    last = props.get("_cdf_version")
    version = latest_version(spark, rec_table)
    full = force_full or last is None or cfg["load_mode"] != LOAD_INCREMENTAL
    # Serverless compute cannot cache/persist, so the plan is evaluated twice (counts, then MERGE).
    # Both evaluations must see the same data: every input is read at a pinned table version.
    if full:
        src = spark.read.option("versionAsOf", version).table(rec_table)
    else:
        if int(last) >= version:
            return {"table": table_node["name"], "mode": "incremental", "changes": 0, "version": version}
        src = (spark.read.option("readChangeFeed", "true").option("startingVersion", int(last) + 1)
               .option("endingVersion", version).table(rec_table))
    staged = prepare_changes(src, cfg, incremental=not full)
    nodes_version = latest_version(spark, nodes_table)
    planned = plan(staged, existing_records(spark, nodes_table, table_node, nodes_version), table_node, cfg, full)
    counts = {r["_op"]: r["n"] for r in planned.groupBy("_op").agg(F.count("*").alias("n")).collect()}
    n_new = int(counts.get("insert", 0))
    first = repo.allocate(n_new)[0] if n_new else 0
    final = finalize(planned, table_node, cfg, first, user)
    view = f"mdm_load_{dataset_key(table_node)}"
    final.createOrReplaceTempView(view)
    if counts:   # Tables load in parallel: a write conflict with another Table's MERGE is retried
        _retry_conflicts(lambda: spark.sql(merge_sql(nodes_table, view, path_key(table_node))).collect())
    fresh = repo.get(table_node["uid"]) or table_node
    fresh = dict(fresh, properties={**(fresh["properties"] or {}), "_cdf_version": version})
    repo.upsert([fresh], user)
    return {"table": table_node["name"], "mode": "full" if full else "incremental", "version": version,
            "inserted": n_new, "updated": int(counts.get("update", 0)),
            "deleted": int(counts.get("delete_soft", 0)) + int(counts.get("delete_hard", 0))}
