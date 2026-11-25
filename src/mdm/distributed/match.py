"""Distributed processing of one matching run (see mdm.matching.run for the steps; this follows them one by one,
with Spark DataFrames in place of in-memory lists)."""

from __future__ import annotations

from ..hierarchy import RuleError
from ..matching.candidates import ALL_PAIRS_LIMIT
from ..matching.run import ROWS_SHOWN, _float
from ..matching.spec import (METHOD_LABELS, STATUS_NOT, STATUS_OPEN, STATUS_SAME, ensure_pairs_folder, load_spec,
                             masters_for_rule)
from ..model import K, clone, now_iso
from . import tasks
from .graph import components
from .io import _lit


def _uids_df(spark, uids, col="uid"):
    return spark.createDataFrame([(u,) for u in sorted(set(uids))], f"{col} string")


def run_match(io, rule_uid: str, mode: str = "incremental", preview: bool = False, user: str = "mdm_match",
              overrides: dict | None = None) -> dict:
    try:
        return _run(io, rule_uid, mode, preview, user, overrides)
    finally:
        io.cleanup()


def _run(io, rule_uid, mode, preview, user, overrides):
    from pyspark.sql import Window
    from pyspark.sql import functions as F
    repo, spark = io.repo, io.spark
    spec = load_spec(repo, rule_uid, overrides)
    errs = spec.validate()
    if errs:
        raise RuleError("; ".join(errs))
    if spec.method == "splink":
        raise RuleError("Exact keys + Splink runs on One Machine processing only")
    full = mode == "full"
    fp = spec.fingerprint()
    keys = [f.key for f in spec.fields]

    # 1 — the records: values, record filter, hashes (Python on the workers)
    rec = io.stage(io.map(io.records([s["uid"] for s in spec.sources]), "prepare_match",
                          {"spec": spec, "fp": fp}, tasks.prepared_schema(spec)), "rec")
    kept = rec.where("keep")
    n_all, n_kept = rec.count(), kept.count()
    dropped = rec.where("NOT keep AND has_match")
    n_dropped = dropped.count()

    # steward decisions (small: what people decided) and the rule's Match Pairs
    pairs_nodes = _pair_nodes(io, spec)
    decided = pairs_nodes.where(f"status IN ({_lit(STATUS_SAME)}, {_lit(STATUS_NOT)})").collect()
    same = [(r["a"], r["b"], _float(r["score_raw"])) for r in decided if r["status"] == STATUS_SAME]
    cannot = [(r["a"], r["b"]) for r in decided if r["status"] == STATUS_NOT]
    dec_uids = {u for p in same for u in p[:2]} | {u for p in cannot for u in p}
    old_of = {r["uid"]: r["old_key"] for r in kept.join(_uids_df(spark, dec_uids), "uid").select("uid", "old_key").collect()} \
        if dec_uids else {}

    # 2 — what changed
    if full:
        changed = kept.select("uid")
        why = {"full": n_kept}
    else:
        ch0 = kept.where("new_hash <> coalesce(old_hash, '') OR old_hash IS NULL OR old_key IS NULL").select("uid")
        pending = set()
        for a, b, _ in same:
            if a in old_of and b in old_of and (old_of[a] != old_of[b] or not old_of[a]):
                pending |= {a, b}
        for a, b in cannot:
            if a in old_of and b in old_of and old_of[a] and old_of[a] == old_of[b]:
                pending |= {a, b}
        ch0 = io.stage(ch0, "ch0")
        pend_df = _uids_df(spark, pending)
        n_ch0 = ch0.count()
        why = {"new_or_changed": n_ch0, "decisions": pend_df.join(ch0, "uid", "left_anti").count() if pending else 0}
        masters = [m["uid"] for m in masters_for_rule(repo, spec.uid)]
        lost_g = (io.children(masters).selectExpr("uid AS g", "explode(coalesce(from_json(get_json_object("
                                                  "properties_json, '$._members'), 'ARRAY<STRING>'), array())) AS m")
                  .join(kept.select(F.col("uid").alias("m")), "m", "left_anti").select("g").distinct()) \
            if masters else spark.createDataFrame([], "g string")
        lost = kept.join(lost_g, kept.old_key == lost_g.g).select("uid")
        before = ch0.unionByName(pend_df).distinct()
        lost = io.stage(lost.distinct(), "lost")
        why["entity_lost_record"] = lost.join(before, "uid", "left_anti").count()
        changed = before.unionByName(lost).distinct()
    changed = io.stage(changed, "changed")
    n_changed = changed.count()

    # 3 — cleaning and blocking keys (DuckDB on the workers)
    clean = io.stage(io.map(kept.select("uid", "src", *[F.col(f"`{k}`") for k in keys]), "clean_match",
                            {"spec": spec}, tasks.cleaned_schema(spec)), "clean")
    n_pairs = 0

    # 4/5 — exact keys, blocking pairs and scores for the records in scope
    def edges_and_scores(scope, both: bool, tag: str):
        nonlocal n_pairs
        s = clean.join(scope.select("uid").withColumn("scope", F.lit(True)), "uid", "left") \
                 .withColumn("scope", F.coalesce("scope", F.lit(False)))
        ex = _exact_edges(spec, s, both)
        sc = None
        if spec.method != "exact":
            pairs = io.stage(_pairs(spec, s, both, n_kept), f"pairs{tag}")
            n_pairs += pairs.count()
            sc = _score(io, spec, clean, pairs)
        return io.stage(ex, f"exact{tag}"), (io.stage(sc, f"scored{tag}") if sc is not None else None)

    empty_ex = spark.createDataFrame([], "a string, b string")
    empty_sc = spark.createDataFrame([], "a string, b string, score double, band string, rule string")
    if n_changed:
        exact1, scored1 = edges_and_scores(changed, False, "1")
        scored1 = scored1 if scored1 is not None else empty_sc
    else:
        exact1, scored1 = empty_ex, empty_sc

    # 6 — the affected set
    if full:
        affected = kept.select("uid")
        exact, scored = exact1, scored1
    else:
        touch = changed.unionByName(exact1.selectExpr("a AS uid")).unionByName(exact1.selectExpr("b AS uid")) \
            .unionByName(scored1.where("band = 'link'").selectExpr("a AS uid")) \
            .unionByName(scored1.where("band = 'link'").selectExpr("b AS uid"))
        ch_set = None
        if same:
            ch_set = {r["uid"] for r in changed.join(_uids_df(spark, dec_uids), "uid").collect()}
            touch = touch.unionByName(_uids_df(spark, [u for a, b, _ in same if a in ch_set or b in ch_set for u in (a, b)]))
        touch = touch.distinct().join(kept.select("uid", "old_key"), "uid")
        keys_t = touch.where("old_key IS NOT NULL").select(F.col("old_key").alias("k")).distinct()
        affected = touch.select("uid").unionByName(kept.join(keys_t, kept.old_key == keys_t.k).select("uid")).distinct()
        affected = io.stage(affected, "affected")
        if affected.join(changed, "uid", "left_anti").limit(1).count():
            exact2, scored2 = edges_and_scores(affected, True, "2")
            exact = exact1.unionByName(exact2).distinct()
            scored = scored1.unionByName(scored2).dropDuplicates(["a", "b"]) if scored2 is not None else scored1
        else:
            exact, scored = exact1, scored1
    affected = io.stage(affected, "affected_all")
    exact, scored = io.stage(exact, "exact"), io.stage(scored, "scored")
    n_aff = affected.count()

    # 7 — entities: connected components, then the constrained union-find per component
    aff = affected.select(F.col("uid").alias("id"))
    inside = lambda df: df.join(aff.withColumnRenamed("id", "a"), "a").join(aff.withColumnRenamed("id", "b"), "b")  # noqa: E731
    ex_in = inside(exact).select("a", "b")
    links_in = inside(scored.where("band = 'link'")).select("a", "b", "score", "rule")
    same_in = inside(spark.createDataFrame(same, "a string, b string, score double")) if same else None
    cannot_in = inside(spark.createDataFrame(cannot, "a string, b string")) if cannot else None
    edges = ex_in.unionByName(links_in.select("a", "b"))
    if same_in is not None:
        edges = edges.unionByName(same_in.select("a", "b"))
    comp = components(io, aff, edges, "cc")
    cid = comp.withColumnRenamed("id", "a")
    parts = [comp.select("comp", F.lit("node").alias("kind"), F.col("id").alias("a"), F.lit(None).cast("string").alias("b"),
                         F.lit(None).cast("double").alias("score"), F.lit(None).cast("string").alias("by")),
             ex_in.join(cid, "a").select("comp", F.lit("exact").alias("kind"), "a", "b",
                                         F.lit(1.0).alias("score"), F.lit("Exact key").alias("by")),
             links_in.join(cid, "a").select("comp", F.lit("link").alias("kind"), "a", "b", "score",
                                            F.col("rule").alias("by"))]
    if same_in is not None:
        parts.append(same_in.join(cid, "a").select("comp", F.lit("same").alias("kind"), "a", "b", "score",
                                                   F.lit("Steward").alias("by")))
    if cannot_in is not None:   # only between records of one component: elsewhere they never meet
        cc = cannot_in.join(cid, "a").join(comp.select(F.col("id").alias("b"), F.col("comp").alias("cb")), "b") \
                      .where("comp = cb")
        parts.append(cc.select("comp", F.lit("cannot").alias("kind"), "a", "b", F.lit(None).cast("double").alias("score"),
                               F.lit(None).cast("string").alias("by")))
    allp = parts[0]
    for p in parts[1:]:
        allp = allp.unionByName(p)
    clustered = io.stage(io.apply(allp.groupBy("comp"), "cluster", {},
                                  "kind string, uid string, gid string, size bigint, score double, by string"), "clustered")
    recs = clustered.where("kind = 'rec'").select("uid", "gid", "size", "score", "by")
    n_blocked = clustered.where("kind = 'blocked'").count()

    # 8 — stable keys: the vote per group of entities that share previous Golden UIDs
    mem = recs.join(kept.select("uid", "old_key"), "uid")
    kedges = mem.where("old_key IS NOT NULL").select(F.concat(F.lit("G:"), "gid").alias("a"),
                                                     F.concat(F.lit("K:"), "old_key").alias("b")).distinct()
    knodes = recs.select(F.concat(F.lit("G:"), "gid").alias("id")).distinct() \
        .unionByName(kedges.select(F.col("b").alias("id")))
    kcomp = components(io, knodes, kedges, "kc")
    kin = mem.join(kcomp.where("id LIKE 'G:%'").select(F.expr("substring(id, 3)").alias("gid"), "comp"), "gid")
    picked = io.stage(io.apply(kin.select("comp", "gid", "uid", "old_key").groupBy("comp"), "keys", {},
                               "kind string, gid string, key string, size bigint"), "picked")
    groups = picked.where("kind = 'group'").select("gid", "key", "size")
    released = sorted(r["key"] for r in picked.where("kind = 'released'").select("key").collect())
    order = Window.orderBy(F.desc("size"), F.asc("gid"))
    if preview:
        named = groups.withColumn("i", F.row_number().over(order)) \
                      .withColumn("key", F.coalesce("key", F.concat(F.lit("new #"), F.col("i").cast("string"))))
        groups = named.select("gid", "key", "size", F.col("key").startswith("new #").alias("is_new"))
    else:
        n_new = groups.where("key IS NULL").count()
        start = repo.allocate_block(n_new) if n_new else 0
        fresh = groups.where("key IS NULL").withColumn("n", F.row_number().over(order) + F.lit(start - 1)) \
                      .select("gid", F.concat(F.lit("MDM-"), F.conv(F.col("n").cast("string"), 10, 36)).alias("new"))
        groups = groups.join(fresh, "gid", "left").select(
            "gid", F.coalesce("key", "new").alias("key"), "size", F.col("key").isNull().alias("is_new"))
    groups = io.stage(groups, "groups")
    stamp = now_iso()
    # results of the affected records: Golden UID, Match Score, Matched By, hash
    res = recs.join(groups.select("gid", "key"), "gid").join(kept.select("uid", "old_key", "new_hash", "src", "name",
                                                                         "source_key"), "uid")
    res = res.select("uid", "gid", "key", "size", "old_key", "new_hash", "src", "name", "source_key",
                     F.col("score").alias("new_score"), F.col("by").alias("new_by"))
    res = io.stage(res, "results")

    # review queue: review-band pairs of different entities, one per pair of entities
    newkey = kept.select("uid", "old_key").join(res.select("uid", "key"), "uid", "left") \
                 .select("uid", F.coalesce("key", "old_key").alias("nk"))
    newkey = io.stage(newkey, "newkey")
    rv = scored.where("band = 'review'") \
        .join(newkey.select(F.col("uid").alias("a"), F.col("nk").alias("ka")), "a", "left") \
        .join(newkey.select(F.col("uid").alias("b"), F.col("nk").alias("kb")), "b", "left") \
        .where(~F.col("ka").eqNullSafe(F.col("kb")))
    ka, kb = F.coalesce(F.col("ka"), F.lit("None")), F.coalesce(F.col("kb"), F.lit("None"))
    rv = rv.select("a", "b", "score", "rule",   # scores come rounded to 4 decimals from the scoring SQL
                   F.concat("a", F.lit("|"), "b").alias("pk"), F.least(ka, kb).alias("e1"), F.greatest(ka, kb).alias("e2"))
    if cannot:
        nk_of = {r["uid"]: r["nk"] for r in newkey.join(_uids_df(spark, dec_uids), "uid").collect()}
        said = {tuple(sorted((str(nk_of.get(a)), str(nk_of.get(b))))) for a, b in cannot}
        rv = rv.join(spark.createDataFrame(sorted(said), "e1 string, e2 string"), ["e1", "e2"], "left_anti")
    best = Window.partitionBy("e1", "e2").orderBy(F.desc("score"), F.asc("pk"))
    review = io.stage(rv.withColumn("rn", F.row_number().over(best)).where("rn = 1").drop("rn", "e1", "e2"), "review")
    n_review = review.count()
    resc = scored.select(F.concat("a", F.lit("|"), "b").alias("pk"))
    aff_a = affected.select(F.col("uid").alias("a"), F.lit(True).alias("ia"))
    aff_b = affected.select(F.col("uid").alias("b"), F.lit(True).alias("ib"))
    ch_a = changed.select(F.col("uid").alias("a"), F.lit(True).alias("ca"))
    ch_b = changed.select(F.col("uid").alias("b"), F.lit(True).alias("cb"))
    pn_touch = pairs_nodes.join(ch_a, "a", "left").join(ch_b, "b", "left").join(aff_a, "a", "left") \
                          .join(aff_b, "b", "left") \
                          .where("ca OR cb OR (ia AND ib)").select("pk")
    rescored = io.stage(resc.unionByName(pn_touch).distinct(), "rescored")

    summary = _summary(spec, kept, dropped, res, groups, released, preview)
    n_links = scored.where("band = 'link'").count()
    stats = {"mode": "full" if full else "incremental", "preview": preview, "method": spec.method,
             "records": n_kept, "filtered_out": n_all - n_kept, "left_by_filter": n_dropped,
             "changed": n_changed, "affected": n_aff, "why": why,
             "pairs_scored": n_pairs, "exact_edges": exact.count(),
             "links": n_links, "review_band": n_review,
             "blocked_by_decisions": n_blocked, **summary["counts"], "model": None}
    out = {"rule": {"uid": spec.uid, "name": spec.rule["name"], "method": METHOD_LABELS[spec.method]},
           "stats": stats, "rows": summary["rows"], "fingerprint": fp}
    if preview:
        out["pairs"] = {"review": n_review}
        return out

    # 9 — write: records, Match Pairs, the rule
    raw = io.records([s["uid"] for s in spec.sources])
    upd = raw.join(res.select("uid", F.col("key").alias("new_key"), "new_score", "new_by", "new_hash")
                   .withColumn("op", F.lit("match")), "uid")
    drop = raw.join(dropped.select("uid"), "uid").select(
        *raw.columns, F.lit(None).cast("string").alias("new_key"), F.lit(None).cast("double").alias("new_score"),
        F.lit(None).cast("string").alias("new_by"), F.lit(None).cast("string").alias("new_hash"), F.lit("drop").alias("op"))
    rec_rows = io.stage(io.map(upd.unionByName(drop), "record_rows", {"stamp": stamp}, "uid string, row_json string"),
                        "rec_rows")
    n_rec = rec_rows.count()
    folder = ensure_pairs_folder(repo, spec, user) if n_review else spec.pairs_folder
    pc = {"new": 0, "updated": 0, "removed": 0}
    writes = [rec_rows]
    dels = None
    if folder:
        pn = pairs_nodes
        fresh = review.join(pn.select("pk"), "pk", "left_anti")
        pc["new"] = fresh.count()
        if pc["new"]:
            start = repo.allocate_block(pc["new"])
            names = kept.select("uid", "name")
            fresh = fresh.withColumn("num", F.row_number().over(Window.orderBy("pk")) + F.lit(start - 1)) \
                         .join(names.select(F.col("uid").alias("a"), F.col("name").alias("name_a")), "a", "left") \
                         .join(names.select(F.col("uid").alias("b"), F.col("name").alias("name_b")), "b", "left")
            writes.append(io.map(fresh, "new_pair_rows", {"folder": folder, "stamp": stamp}, "uid string, row_json string"))
        is_open = f"(status IS NULL OR status = '' OR status = {_lit(STATUS_OPEN)})"
        again = pn.where(is_open).join(review.select("pk", F.col("score").alias("new_score"),
                                                     F.col("rule").alias("new_rule")), "pk")
        if again.limit(1).count():
            upd_pairs = io.stage(io.map(io.children([folder["uid"]]).join(again.select("uid", "new_score", "new_rule"), "uid"),
                                        "updated_pair_rows", {"stamp": stamp}, "uid string, row_json string"), "pair_upd")
            pc["updated"] = upd_pairs.count()
            writes.append(upd_pairs)
        dels = io.stage(pn.where(is_open).join(rescored, "pk").join(review.select("pk"), "pk", "left_anti")
                          .select("uid", "path_key"), "pair_del")
        pc["removed"] = dels.count()
    rule = clone(repo.get(spec.uid) or spec.rule)
    rule["properties"]["_config_hash"] = fp
    rule["properties"]["Last Run"] = (f"{stamp} · {stats['mode']} · {stats['records']} records, "
                                      f"{stats['changed']} re-matched, {pc['new']} new pairs to review")
    allw = writes[0]
    for w in writes[1:]:
        allw = allw.unionByName(w)
    if n_rec or pc["new"] or pc["updated"]:
        io.merge_rows(allw)
    repo.upsert([rule], user)
    if dels is not None and pc["removed"]:
        io.delete_rows(dels)
    open_before = pairs_nodes.where(f"status IS NULL OR status = '' OR status = {_lit(STATUS_OPEN)}").count()
    stats.update(records_written=n_rec, pairs_new=pc["new"], pairs_updated=pc["updated"],
                 pairs_removed=pc["removed"], open_pairs=open_before + pc["new"] - pc["removed"])
    out["pairs"] = pc
    return out


# ---------------------------------------------------------------------------- pieces
def _pair_nodes(io, spec):
    """The rule's Match Pair nodes: uid, path_key, a, b, pk, status, score_raw (one per pair, like decisions())."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F
    if not spec.pairs_folder:
        return io.spark.createDataFrame([], "uid string, path_key string, a string, b string, pk string, "
                                            "status string, score_raw string")
    df = io.nodes().where(f"parent_uid = {_lit(spec.pairs_folder['uid'])} AND NOT is_deleted "
                          f"AND array_contains(types, {_lit(K.NT_MATCH_PAIR)})")
    df = df.selectExpr("uid", "path_key", "sort_order", "uid_num",
                       "try_variant_get(properties, '$[\\'Record A\\']', 'string') AS a",
                       "try_variant_get(properties, '$[\\'Record B\\']', 'string') AS b",
                       "try_variant_get(properties, '$.Status', 'string') AS status",
                       "try_variant_get(properties, '$.Score', 'string') AS score_raw") \
           .where("a IS NOT NULL AND a <> '' AND b IS NOT NULL AND b <> ''") \
           .withColumn("pk", F.concat(F.least("a", "b"), F.lit("|"), F.greatest("a", "b")))
    last = Window.partitionBy("pk").orderBy(F.col("sort_order").isNull().desc(), F.desc("sort_order"), F.desc("uid_num"))
    return io.stage(df.withColumn("rn", F.row_number().over(last)).where("rn = 1")
                      .select("uid", "path_key", "a", "b", "pk", "status", "score_raw"), "pairs_nodes")


def _exact_edges(spec, s, both: bool):
    """Star edges (smallest uid of the group, member) per exact key, for groups with a record in scope."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F
    out = None
    for f in spec.exact_fields():
        k = F.col(f"`{f.key}`")
        g = s.where(k.isNotNull())
        if both:
            g = g.where("scope")
        hit = s.where(k.isNotNull() & F.col("scope")).select(k.alias("v")).distinct()
        g = g.select("uid", k.alias("v"), F.min("uid").over(Window.partitionBy(k)).alias("root"))
        e = g.join(hit, "v").where("uid <> root").select(F.col("root").alias("a"), F.col("uid").alias("b"))
        out = e if out is None else out.unionByName(e)
    return (out if out is not None else s.sparkSession.createDataFrame([], "a string, b string")).distinct()


def _pairs(spec, s, both: bool, n_kept: int):
    from pyspark.sql import functions as F
    need = (F.col("l.scope") & F.col("r.scope")) if both else (F.col("l.scope") | F.col("r.scope"))
    rules = tasks.block_columns(spec)
    if not rules:
        if n_kept > ALL_PAIRS_LIMIT:
            n = n_kept
            raise ValueError(f"{n} records and no blocking rule — add blocking rules (Blocking tab) "
                             f"so that not all {n * (n - 1) // 2:,} pairs are compared")
        return s.alias("l").join(s.alias("r"), F.col("l.uid") < F.col("r.uid")).where(need) \
                .select(F.col("l.uid").alias("a"), F.col("r.uid").alias("b"))
    out = None
    for _, cols in rules:
        side = s.where(" AND ".join(f"{c} IS NOT NULL" for c in cols))
        cond = F.col("l.uid") < F.col("r.uid")
        for c in cols:
            cond = cond & (F.col(f"l.{c}") == F.col(f"r.{c}"))
        p = side.alias("l").join(side.alias("r"), cond).where(need).select(F.col("l.uid").alias("a"), F.col("r.uid").alias("b"))
        out = p if out is None else out.unionByName(p)
    return out.distinct()


def _score(io, spec, clean, pairs):
    from pyspark.sql import functions as F
    side = lambda suffix, col: clean.select(F.col("uid").alias(col), *[F.col(f"`{f.key}`").alias(f"{f.key}{suffix}")  # noqa: E731
                                                                       for f in spec.fields])
    cmp = pairs.join(side("_l", "a"), "a").join(side("_r", "b"), "b") \
               .select("a", "b", *[f"{f.key}{s}" for f in spec.fields for s in ("_l", "_r")])
    return io.map(cmp, "score_fuzzy", {"spec": spec}, "a string, b string, score double, band string, rule string")


def _summary(spec, kept, dropped, res, groups, released, preview):
    """mdm.matching.run._summary: counts over all entities in Spark, the first ROWS_SHOWN rows in detail."""
    from pyspark.sql import functions as F
    src_name = {s["uid"]: s["name"] for s in spec.sources}
    members_of = kept.where("old_key IS NOT NULL").groupBy(F.col("old_key").alias("key")).agg(F.count("*").alias("before"))
    out_of = dropped.where("old_key IS NOT NULL").groupBy(F.col("old_key").alias("key")).agg(F.count("*").alias("out"))
    per = res.groupBy("gid").agg(
        F.sum(F.when(F.col("old_key") == F.col("key"), 1).otherwise(0)).alias("n_same"),
        F.countDistinct(F.when(F.col("old_key").isNotNull() & (F.col("old_key") != F.col("key")), F.col("old_key"))).alias("n_other"),
        F.max(F.col("old_key").isNotNull().cast("int")).alias("has_old"))
    g = groups.join(per, "gid").join(members_of, "key", "left").join(out_of, "key", "left") \
              .fillna(0, ["before", "out"])
    g = g.withColumn("out", F.when(F.col("is_new"), F.lit(0)).otherwise(F.col("out"))) \
         .withColumn("before", F.when(F.col("is_new"), F.lit(0)).otherwise(F.col("before")))
    action = (F.when(~F.col("is_new") & (F.col("n_same") == F.col("size")) & (F.col("before") == F.col("size"))
                     & (F.col("out") == 0), "Unchanged")
              .when(F.col("is_new") & (F.col("has_old") == 0), "New entity")
              .when(F.col("is_new"), "Split off")
              .when(F.col("n_other") > 0, "Merged")
              .when((F.col("size") > F.col("n_same")) & ((F.col("before") > F.col("n_same")) | (F.col("out") > 0)), "Changed")
              .when(F.col("size") > F.col("n_same"), "Record joined")
              .otherwise("Record left"))
    g = g.withColumn("action", action)
    tally = {r["action"]: r["n"] for r in g.groupBy("action").agg(F.count("*").alias("n")).collect()}
    counts = {"new_entities": tally.get("New entity", 0),
              "changed_entities": sum(tally.get(a, 0) for a in ("Changed", "Record joined", "Record left")),
              "merged": tally.get("Merged", 0), "split": tally.get("Split off", 0),
              "unchanged": tally.get("Unchanged", 0), "retired": len(released)}
    # entities whose records are all left out by the record filter
    kept_keys = groups.select("key").unionByName(kept.where("old_key IS NOT NULL").select(F.col("old_key").alias("key")))
    filt = out_of.join(kept_keys, "key", "left_anti").select("key", "out")
    rel_set = set(released)
    filt_rows = sorted((r["key"], r["out"]) for r in filt.collect() if r["key"] not in rel_set)
    counts["retired"] += len(filt_rows)

    rows = []
    shown = g.where("action <> 'Unchanged'").orderBy(F.desc("size"), F.asc("gid")).limit(ROWS_SHOWN).collect()
    if shown:
        ids = [r["gid"] for r in shown]
        mem = res.where(F.col("gid").isin(ids)).collect()
        by_g: dict[str, list] = {}
        for m in mem:
            by_g.setdefault(m["gid"], []).append(m)
        for r in shown:
            k, ms = r["key"], sorted(by_g[r["gid"]], key=lambda m: (m["src"], m["uid"]))
            olds = sorted({m["old_key"] for m in ms if m["old_key"]} - {k})
            scores = [m["new_score"] for m in ms if m["new_score"] is not None]
            whys = sorted({m["new_by"] for m in ms if m["new_by"] != "Alone"})
            if olds:
                whys.append("joined " + ", ".join(olds))
            if r["out"]:
                whys.append(f"{r['out']} record(s) left out by the record filter")
            rows.append({"uid": k, "name": ms[0]["name"], "action": r["action"], "new": bool(r["is_new"]),
                         "records": [{"uid": m["uid"], "label": f"{src_name.get(m['src'], '?')} · {m['source_key'] or m['name']}",
                                      "name": m["name"], "moved": m["old_key"] != k} for m in ms],
                         "score": min(scores) if scores else None, "why": "; ".join(whys) or "no candidate linked"})
    for k in released:
        if len(rows) < ROWS_SHOWN:
            rows.append({"uid": k, "name": k, "action": "Retired", "new": False, "records": [], "score": None,
                         "why": "no records left — survivorship retires its golden record"})
    for k, n in filt_rows:
        if len(rows) < ROWS_SHOWN:
            rows.append({"uid": k, "name": k, "action": "Retired", "new": False, "records": [], "score": None,
                         "why": f"all {n} record(s) left out by the record filter — "
                                "survivorship retires its golden record"})
    order = {"Merged": 0, "Split off": 1, "Changed": 2, "Record joined": 3, "Record left": 4, "New entity": 5, "Retired": 6}
    rows.sort(key=lambda x: (order.get(x["action"], 9), x["name"]))
    return {"rows": rows, "counts": counts}
