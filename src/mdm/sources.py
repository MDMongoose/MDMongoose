"""Reading source tables for Configure Load: catalog browser and 10-row preview."""

from __future__ import annotations

import re

_IDENT = re.compile(r"^[A-Za-z0-9_\-]+$")


def _q(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise ValueError(f"bad identifier {name!r}")
    return f"`{name}`"


class SqlSourceReader:
    """Unity Catalog via SQL (SQL warehouse in the app)."""

    def __init__(self, executor):
        self.x = executor

    @staticmethod
    def _col(rows, *names):
        out = []
        for r in rows:
            for n in names:
                if n in r and r[n] is not None:
                    out.append(r[n])
                    break
        return out

    def catalogs(self) -> list[str]:
        return self._col(self.x.query("SHOW CATALOGS"), "catalog", "catalog_name")

    def schemas(self, catalog: str) -> list[str]:
        return self._col(self.x.query(f"SHOW SCHEMAS IN {_q(catalog)}"), "databaseName", "namespace", "schema_name")

    def tables(self, catalog: str, schema: str) -> list[str]:
        return self._col(self.x.query(f"SHOW TABLES IN {_q(catalog)}.{_q(schema)}"), "tableName", "table_name")

    def sample(self, full_name: str, limit: int = 10) -> tuple[list[str], list[dict]]:
        parts = full_name.split(".")
        if len(parts) != 3:
            raise ValueError("table must be catalog.schema.table")
        rows = self.x.query(f"SELECT * FROM {'.'.join(_q(p) for p in parts)} LIMIT {int(limit)}")
        cols = list(rows[0].keys()) if rows else self._columns(parts)
        return cols, rows

    def has_cdf(self, full_name: str) -> bool:
        """True when the table has delta.enableChangeDataFeed = true."""
        parts = full_name.split(".")
        if len(parts) != 3:
            return False
        try:
            rows = self.x.query(f"SHOW TBLPROPERTIES {'.'.join(_q(p) for p in parts)}")
        except Exception:  # noqa: BLE001 - missing table / no access
            return False
        for r in rows:
            if str(r.get("key")) == "delta.enableChangeDataFeed":
                return str(r.get("value")).lower() == "true"
        return False

    def _columns(self, parts) -> list[str]:
        rows = self.x.query(f"DESCRIBE TABLE {'.'.join(_q(p) for p in parts)}")
        return [r["col_name"] for r in rows if r.get("col_name") and not str(r["col_name"]).startswith("#")]


class DemoSourceReader:
    """MDM_LOCAL=1: the demo source tables only."""

    def __init__(self, catalog: str = "workspace", schema: str | None = None):
        from . import names
        from .seed import DEMO_SOURCE_ROWS
        self.cat, self.sch, self.data = catalog, schema or names.STAGING, DEMO_SOURCE_ROWS

    def catalogs(self):
        return [self.cat]

    def schemas(self, catalog):
        return [self.sch] if catalog == self.cat else []

    def tables(self, catalog, schema):
        return sorted(self.data) if (catalog, schema) == (self.cat, self.sch) else []

    def has_cdf(self, full_name):
        return full_name.split(".")[-1] in self.data

    def sample(self, full_name, limit=10):
        spec = self.data.get(full_name.split(".")[-1])
        if not spec:
            raise ValueError(f"{full_name} not found")
        return spec["columns"], [dict(zip(spec["columns"], r)) for r in spec["rows"][:limit]]
