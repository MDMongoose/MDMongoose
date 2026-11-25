"""Connected components in Spark SQL, without GraphFrames (serverless compute cannot load its JAR).

Min-label propagation with pointer jumping: every node starts with its own id as label, takes the smallest
label among its neighbours, then the label of its label. The label converges to the smallest id of the
component; the number of rounds grows with the logarithm of the longest chain, so a few rounds for entities.
Every round is written to a scratch table, so the query plan does not grow with the rounds.
"""

from __future__ import annotations

MAX_ROUNDS = 100


def components(io, nodes, edges, name: str):
    """nodes: DataFrame(id); edges: DataFrame(a, b). Returns DataFrame(id, comp), comp = smallest id."""
    from pyspark.sql import functions as F
    e = edges.select(F.col("a").alias("src"), F.col("b").alias("dst"))
    e = io.stage(e.unionByName(e.select(F.col("dst").alias("src"), F.col("src").alias("dst")))
                 .where("src <> dst").distinct(), f"{name}_e")
    lab = io.stage(nodes.select("id").distinct().withColumn("label", F.col("id")), f"{name}_l0")
    for i in range(1, MAX_ROUNDS + 1):
        near = (e.join(lab.select(F.col("id").alias("dst"), F.col("label").alias("nl")), "dst")
                 .groupBy(F.col("src").alias("id")).agg(F.min("nl").alias("nl")))
        step = (lab.join(near, "id", "left")
                   .select("id", F.least(F.col("label"), F.coalesce(F.col("nl"), F.col("label"))).alias("label")))
        jump = (step.alias("x").join(step.select(F.col("id").alias("lid"), F.col("label").alias("ll")).alias("y"),
                                     F.col("x.label") == F.col("y.lid"), "left")
                    .select(F.col("x.id").alias("id"),
                            F.least(F.col("x.label"), F.coalesce(F.col("y.ll"), F.col("x.label"))).alias("label")))
        new = io.stage(jump, f"{name}_l{i % 2}")   # rounds alternate between two scratch tables
        moved = new.join(lab.select("id", F.col("label").alias("old")), "id").where("label <> old").limit(1).count()
        lab = new
        if not moved:
            break
    return lab.select("id", F.col("label").alias("comp"))
