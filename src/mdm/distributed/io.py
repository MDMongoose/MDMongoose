"""MDMNodes for Distributed processing: reads as Spark DataFrames, set-based writes, scratch tables, tasks.

Writes are node rows as JSON (mdm.sqlrepo.node_to_row: the same row a One Machine upsert writes), merged with
one MERGE per step. Intermediate results are written to scratch tables next to MDMNodes (mdm_tmp_<run>_<name>,
dropped at the end), so a step's Python work is not repeated by every later step that reads it.
"""

from __future__ import annotations

import os
import pickle
import tempfile
import uuid
import zipfile
from pathlib import Path

from ..model import K
from ..sqlrepo import ROW_SCHEMA, SELECT_COLS, UPDATE_COLS

ROW_ELEMENT = ROW_SCHEMA[len("ARRAY<"):-1]   # the STRUCT of one node row
SRC = str(Path(__file__).resolve().parents[2])   # the folder that holds the mdm package
_shipped: set[int] = set()


def ship_code(spark) -> None:
    """Make the mdm (and mdm_custom) packages importable on the Spark workers: a zip of them as a Python
    artifact (Spark Connect / serverless) or a py-file (classic Spark). Workers that can read SRC use it too."""
    if id(spark) in _shipped:
        return
    _shipped.add(id(spark))
    path = Path(tempfile.mkdtemp(prefix="mdm_code_")) / "mdm_code.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for pkg in ("mdm", "mdm_custom"):
            root = Path(SRC) / pkg
            for f in root.rglob("*.py") if root.is_dir() else []:
                z.write(f, f.relative_to(SRC))
    try:
        spark.addArtifacts(str(path), pyfile=True)
        return
    except Exception:  # noqa: BLE001 - classic Spark, or artifacts not allowed: try the next way
        pass
    try:
        spark.sparkContext.addPyFile(str(path))
    except Exception:  # noqa: BLE001 - the workers read SRC (local Spark, workspace files)
        pass


class SparkNodes:
    """MDMNodes as seen by a Distributed run: `table` is its full name; `repo` serves the small reads and
    writes (rules, the UID counter, one rule node)."""

    def __init__(self, spark, table: str, repo, user: str = "mdm_core"):
        from pyspark import cloudpickle
        from . import worker
        cloudpickle.register_pickle_by_value(worker)
        self.spark, self.table, self.repo, self.user = spark, table, repo, user
        self.run_id = uuid.uuid4().hex[:8]
        self._tmp: list[str] = []
        ship_code(spark)

    # -- reads ---------------------------------------------------------------
    def nodes(self):
        return self.spark.table(self.table)

    def select(self, where: str):
        """Node rows (the columns of mdm.sqlrepo.SELECT_COLS, plus path_key) matching a SQL condition."""
        return self.nodes().where(where).selectExpr(*[c.strip() for c in _split_cols(SELECT_COLS)], "path_key")

    def records(self, table_uids: list[str]):
        """The source records of these Tables (live, loaded or of the Record type)."""
        ids = ", ".join(_lit(u) for u in table_uids) or "NULL"
        return self.select(f"parent_uid IN ({ids}) AND NOT is_deleted "
                           f"AND (origin = 'loaded' OR array_contains(types, {_lit(K.NT_RECORD)}))")

    def children(self, parent_uids: list[str], live: bool = True):
        ids = ", ".join(_lit(u) for u in parent_uids) or "NULL"
        return self.select(f"parent_uid IN ({ids})" + (" AND NOT is_deleted" if live else ""))

    # -- tasks ---------------------------------------------------------------
    def map(self, df, task: str, payload, schema: str):
        from .worker import mapper
        return df.mapInPandas(mapper(task, SRC, pickle.dumps(payload)), schema)

    def apply(self, grouped, task: str, payload, schema: str):
        from .worker import grouper
        return grouped.applyInPandas(grouper(task, SRC, pickle.dumps(payload)), schema)

    # -- scratch tables ------------------------------------------------------
    def stage(self, df, name: str):
        """Write an intermediate result once and read it back."""
        schema = self.table.rsplit(".", 1)[0]
        full = f"{schema}.mdm_tmp_{self.run_id}_{name}"
        df.write.mode("overwrite").saveAsTable(full)
        if full not in self._tmp:
            self._tmp.append(full)
        return self.spark.table(full)

    def cleanup(self) -> None:
        for t in self._tmp:
            try:
                self.spark.sql(f"DROP TABLE IF EXISTS {t}")
            except Exception:  # noqa: BLE001 - a leftover scratch table is harmless
                pass
        self._tmp = []

    # -- writes --------------------------------------------------------------
    def merge_rows(self, df) -> int:
        """Upsert node rows (column row_json: mdm.sqlrepo.node_to_row as JSON) with one MERGE."""
        from pyspark.sql import functions as F
        rows = df.select(F.from_json("row_json", ROW_ELEMENT).alias("s")).select("s.*")
        view = f"mdm_rows_{self.run_id}_{uuid.uuid4().hex[:6]}"
        rows.createOrReplaceTempView(view)
        sets = ", ".join(f"t.{c} = s.{c}" for c in UPDATE_COLS)
        cols = ", ".join(["uid"] + UPDATE_COLS + ["properties", "modified_at", "modified_by"])
        vals = ", ".join(["s.uid"] + [f"s.{c}" for c in UPDATE_COLS]
                         + ["parse_json(s.properties_json)", "current_timestamp()", ":user"])
        res = self.spark.sql(f"""MERGE INTO {self.table} AS t USING {view} AS s
ON t.uid = s.uid AND t.path_key = s.path_key
WHEN MATCHED THEN UPDATE SET {sets}, t.properties = parse_json(s.properties_json),
  t.modified_at = current_timestamp(), t.modified_by = :user
WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({vals})""", args={"user": self.user}).collect()
        self.spark.catalog.dropTempView(view)
        r = res[0].asDict() if res else {}
        return int(r.get("num_updated_rows") or 0) + int(r.get("num_inserted_rows") or 0)

    def delete_rows(self, df) -> int:
        """Hard-delete the nodes in df (columns uid, path_key) with one MERGE."""
        view = f"mdm_del_{self.run_id}_{uuid.uuid4().hex[:6]}"
        df.select("uid", "path_key").createOrReplaceTempView(view)
        res = self.spark.sql(f"MERGE INTO {self.table} AS t USING {view} AS s "
                             "ON t.uid = s.uid AND t.path_key = s.path_key WHEN MATCHED THEN DELETE").collect()
        self.spark.catalog.dropTempView(view)
        r = res[0].asDict() if res else {}
        return int(r.get("num_deleted_rows") or r.get("num_affected_rows") or 0)


def _lit(s: str) -> str:
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _split_cols(cols: str) -> list[str]:
    """SELECT_COLS split at the top-level commas."""
    out, depth, cur = [], 0, ""
    for ch in cols:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [c.replace("\n", " ").strip() for c in out]


def local_tmp_dir() -> str:
    return tempfile.mkdtemp(prefix="mdm_stage_", dir=os.environ.get("MDM_TMP"))
