"""Distributed processing of Refresh Golden Records (mdm.golden.refresh): every entity is built by
golden.build_golden on a Spark worker; golden records and their source links are written with one MERGE each."""

from __future__ import annotations

import json

from ..hierarchy import RuleError
from ..model import now_iso
from ..uid import to_uid

SOURCE_LINK_TYPE = to_uid(1333)   # LinkTypes · source


def refresh_golden(io, catalog, master_uid: str, user: str = "mdm_golden", retire: bool = True) -> dict:
    try:
        return _refresh(io, catalog, master_uid, user, retire)
    finally:
        io.cleanup()


def _refresh(io, catalog, master_uid, user, retire):
    from pyspark.sql import Window
    from pyspark.sql import functions as F
    from .. import golden
    repo = io.repo
    master = repo.get(master_uid)
    if not master:
        raise RuleError(f"{master_uid} not found")
    rule_uid = (master["properties"] or {}).get("Survivorship Rule")
    if not rule_uid:
        raise RuleError("the Master Data node has no Survivorship Rule")
    ctx = golden.RuleContext(repo, catalog, rule_uid, load_records=False)
    if not ctx.cfgs:
        raise RuleError("the rule has no Property Rules for the Golden Record Type's properties")
    wctx = ctx.detach()
    src_idx = {s["uid"]: i for i, s in enumerate(ctx.sources)}
    stamp = now_iso()

    # source records -> Golden UID and their One Machine order
    rec = io.stage(io.map(io.records(list(src_idx)), "prepare_golden", {"filters": ctx.filters, "src_idx": src_idx},
                          "uid string, keep boolean, golden_key string, o1 int, o2 boolean, o3 double, o4 bigint, "
                          "node_json string"), "g_rec")
    rec = rec.where("keep")
    unmatched = rec.where("golden_key IS NULL").count()
    members = rec.where("golden_key IS NOT NULL").select(F.col("golden_key").alias("target"), F.lit("m").alias("role"),
                                                         "o1", "o2", "o3", "o4", "node_json")
    targets = members.select("target").distinct()
    existing = io.stage(io.map(io.children([master["uid"]]).where("origin = 'generated'"), "node_json_rows", {},
                               "uid string, locked boolean, node_json string"), "g_existing")
    old = existing.join(targets.withColumnRenamed("target", "uid"), "uid").select(
        F.col("uid").alias("target"), F.lit("old").alias("role"), *_no_order(F), "node_json")
    others = io.map(io.select("uid IS NOT NULL").join(targets.withColumnRenamed("target", "uid"), "uid")
                    .join(existing.select("uid"), "uid", "left_anti"), "node_json_rows", {},
                    "uid string, locked boolean, node_json string")
    other = others.select(F.col("uid").alias("target"), F.lit("other").alias("role"), *_no_order(F), "node_json")
    entities = io.stage(members.unionByName(old).unionByName(other), "g_entities")

    # the values the Enum / Link properties look up: once, on the driver
    schema = ("target string, kind string, status string, action string, write boolean, n_members bigint, "
              "conflict string, conflicts_json string, row_json string, base string, arg string, text string")
    payload = {"ctx": wctx, "master": master, "stamp": stamp, "resolved": None}
    calls = io.apply(entities.groupBy("target"), "golden_entity", payload, schema) \
              .select("base", "arg", "text").distinct().collect()
    resolve = catalog.resolver()
    resolved = {json.dumps([c["base"], c["arg"], c["text"]]): list(resolve(c["base"], c["arg"], c["text"]))
                for c in calls}
    payload["resolved"] = resolved
    built = io.stage(io.apply(entities.groupBy("target"), "golden_entity", payload, schema).where("kind = 'entity'"),
                     "g_built")

    ok = built.where("status = 'ok'")
    agg = {r["action"]: (r["n"], r["m"]) for r in ok.groupBy("action").agg(F.count("*").alias("n"),
                                                                            F.sum("n_members").alias("m")).collect()}
    n_golden = sum(n for n, _ in agg.values())
    n_records = sum(m or 0 for _, m in agg.values())
    conflicts = []
    for r in built.where("status = 'conflict' OR (conflicts_json IS NOT NULL AND conflicts_json <> '[]')") \
                  .select("target", "status", "conflict", "conflicts_json").collect():
        conflicts.append((r["target"], [r["conflict"]] if r["status"] == "conflict" else json.loads(r["conflicts_json"])))
    conflicts = [c for _, cs in sorted(conflicts) for c in cs]
    used = ok.select(F.col("target").alias("uid"))
    gone = existing.join(used, "uid", "left_anti")
    retired = gone.where("NOT locked") if retire else gone.limit(0)
    n_retired = retired.count()

    # write the golden records that changed and the retired ones
    writes = ok.where("write").select(F.col("target").alias("uid"), "row_json")
    if n_retired:
        writes = writes.unionByName(io.map(io.children([master["uid"]]).join(retired.select("uid"), "uid"),
                                           "deleted_rows", {}, "uid string, row_json string"))
    if writes.limit(1).count():
        io.merge_rows(writes)

    # source links: one Child Link per source record below every golden record (golden.source_links)
    gpath = list(master["path"]) + [master["uid"]]
    want = rec.where("golden_key IS NOT NULL").join(used.withColumnRenamed("uid", "golden_key"), "golden_key") \
              .select(F.col("golden_key").alias("target"), "uid", F.get_json_object("node_json", "$.parent_uid").alias("pu"),
                      F.get_json_object("node_json", "$.name").alias("name"))
    want = want.withColumn("i", F.row_number().over(Window.partitionBy("target").orderBy("pu", "uid")) - 1)
    parents = used.unionByName(retired.select("uid"))
    have = io.nodes().where("kind = 'Link' AND NOT is_deleted").join(parents.withColumnRenamed("uid", "parent_uid"),
                                                                       "parent_uid") \
             .selectExpr("uid", "path_key", "parent_uid", "sort_order", "uid_num",
                         "try_variant_get(properties, '$[\\'Linked Node\\']', 'string') AS link_to")
    last = Window.partitionBy("parent_uid", "link_to").orderBy(F.col("sort_order").isNull().desc(), F.desc("sort_order"),
                                                               F.desc("uid_num"))
    have = io.stage(have.withColumn("rn", F.row_number().over(last)).where("rn = 1").drop("rn"), "g_links")
    todo = want.join(have.select(F.col("parent_uid").alias("target"), F.col("link_to").alias("uid")),
                     ["target", "uid"], "left_anti")
    todo = io.stage(todo, "g_todo")
    n_todo = todo.count()
    drop = have.join(want.select(F.col("target").alias("parent_uid"), F.col("uid").alias("link_to")),
                     ["parent_uid", "link_to"], "left_anti")
    link_writes = None
    if n_todo:
        start = repo.allocate_block(n_todo)
        todo = todo.withColumn("num", F.row_number().over(Window.orderBy("target", "i")) + F.lit(start - 1))
        link_writes = io.map(todo, "new_link_rows", {"gpath": gpath, "source": SOURCE_LINK_TYPE},
                             "uid string, row_json string")
    if drop.limit(1).count():
        d = io.map(io.select("kind = 'Link'").join(drop.select("uid"), "uid"), "deleted_rows", {},
                   "uid string, row_json string")
        link_writes = d if link_writes is None else link_writes.unionByName(d)
    if link_writes is not None:
        io.merge_rows(link_writes)

    created = agg.get("Create", (0, 0))[0]
    upd, unch = agg.get("Update", (0, 0))[0], agg.get("Unchanged", (0, 0))[0]
    return {"master": master["name"], "records": n_records, "golden": n_golden, "created": created,
            "updated": upd + unch, "changed": upd, "retired": n_retired, "unmatched": unmatched,
            "conflicts": conflicts}


def _no_order(F):
    return [F.lit(0).alias("o1"), F.lit(False).alias("o2"), F.lit(0.0).alias("o3"), F.lit(0).cast("bigint").alias("o4")]
