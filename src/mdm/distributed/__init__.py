"""Distributed processing: matching and survivorship of one Master Data node on a Spark cluster.

The same rules give the same result as One Machine processing (mdm.matching.run, mdm.golden): every step that
decides something runs the same code, only spread over the cluster.

    step                        One Machine processing          Distributed processing
    read records, record filter Python, all records in memory   Spark workers, per partition (same Python)
    cleaning, comparisons       DuckDB on one machine            DuckDB on every Spark worker, per partition
    exact keys, blocking pairs  DuckDB SQL                       Spark joins on the cleaned values
    entities                    union-find over all records      connected components in Spark, then the same
                                                                 union-find (Not-the-same decisions) per component
    stable Golden UIDs          majority vote, largest first     the same vote per group of entities that share
                                                                 a previous Golden UID; new UIDs in the same order
    survivorship                one entity after another         the same per-entity code on the Spark workers
    writes                      MERGE batches of 2 000 rows      one MERGE per step from a DataFrame

Splink runs on One Machine processing only (its training needs the whole sample on one machine).
"""

from .match import run_match  # noqa: F401
from .survive import refresh_golden  # noqa: F401
