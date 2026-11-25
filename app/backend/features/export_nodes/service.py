"""Export nodes: views, materialized views and files kept up to date."""

from __future__ import annotations

from mdm import names
from mdm.hierarchy import RuleError, chain_of
from mdm.model import K
from mdm.permissions import READER, User
from mdm.uid import to_uid as _uid

from ...core.service import (
    Forbidden,
)


class ExportNodesService:
    def _prepare_export(self, user: User, n: dict, old: dict | None) -> None:
        """Validate an Export node before it is stored; fill Object Name and Location."""
        from mdm import exports
        cat = self._cat()
        name_of = cat.namer()
        spec = exports.spec_of(n, name_of)
        exports.validate(spec, n["uid"], exports.export_nodes(self.repo), name_of)
        root = self.repo.get(spec["root"])
        if not root:
            raise RuleError("the Root Node does not exist")
        if self._level(user, chain_of(self.repo, root)) < READER:
            raise Forbidden(f"you cannot read the Root Node '{root['name']}'")
        if spec["kind"] in ("mv", "view"):
            prev = exports.spec_of(old, name_of) if old and K.NT_EXPORT in old["types"] else None
            ours = prev and prev["name"] == spec["name"] and prev["kind"] in ("mv", "view")
            if prev and ours and prev["kind"] != spec["kind"]:
                raise RuleError(f"{spec['name']} already exists as a {exports.KIND_LABEL[prev['kind']].lower()} — "
                                "use a new Object Name to change the Export Type")
            x = self._sql()
            if x is not None and not ours:
                hit = x.query(f"SELECT table_type FROM `{self.catalog_name}`.information_schema.tables "
                              "WHERE table_schema = :s AND table_name = :n", {"s": names.EXPORT, "n": spec["name"]})
                if hit:
                    raise RuleError(f"{self.catalog_name}.{names.EXPORT}.{spec['name']} already exists — choose another Object Name")
        p = n.setdefault("properties", {})
        for k, v in (("Include Properties", True), ("Include UIDs", True), ("Balanced Flag", False),
                     ("Balanced", _uid(1361))):
            if p.get(k) in (None, ""):
                p[k] = v
        if spec["kind"] == "csv" and not p.get("CSV Delimiter"):
            p["CSV Delimiter"] = ","
        if spec["kind"] == "excel":
            p.setdefault("Excel Sheets", _uid(1391))
            p.setdefault("Excel Header", _uid(1396))
        p["Object Name"] = spec["name"]
        p["Location"] = exports.location(spec, self.catalog_name)
        if p.get("Child Depth") in (None, ""):
            p["Child Depth"] = spec["depth"]

    def _drop_export_object(self, node: dict) -> None:
        from mdm import exports
        spec = exports.spec_of(node, self._cat().namer())
        x = self._sql()
        if x is not None and spec["kind"] == "view" and exports.NAME_RE.match(spec["name"]):
            x.execute(f"DROP VIEW IF EXISTS `{self.catalog_name}`.`{names.EXPORT}`.`{spec['name']}`")

    def export_sql(self, node: dict) -> str | None:
        from mdm import exports
        cat = self._cat()
        spec = exports.spec_of(node, cat.namer())
        if spec["kind"] not in ("mv", "view") or not spec["root"]:
            return None
        try:
            return exports.build_sql(self.repo, cat, spec, getattr(self.repo, "table", "") or names.nodes_table())[0]
        except RuleError as e:
            return f"-- {e}"

    def _after_export_saved(self, user: User, n: dict, old: dict | None) -> None:
        """A View is (re)created right away; materialized views and files are refreshed by the mdm_export job."""
        from mdm import exports
        cat = self._cat()
        spec = exports.spec_of(n, cat.namer())
        if old is not None and K.NT_EXPORT in old["types"]:
            prev = exports.spec_of(old, cat.namer())
            if prev["kind"] == "view" and (prev["name"] != spec["name"] or spec["kind"] != "view"):
                self._drop_export_object(old)
        self.refresh_export(user, n["uid"])

    def refresh_export(self, user: User, uid: str) -> dict:
        """Run Export / save: Views are created here, by the app (so the app's service principal owns."""
        from mdm import exports
        from mdm.model import now_iso
        cat = self._cat()
        n = self._node(uid)
        spec = exports.spec_of(n, cat.namer())
        x = self._sql()
        run: dict = {"state": "SUCCESS"}
        if spec["kind"] == "view":
            if x is not None:
                sql, cols = exports.build_sql(self.repo, cat, spec, self.repo.table)
                x.execute(exports.view_ddl(sql, self.catalog_name, spec["name"], n))
                status = f"{now_iso()} · view created by {user.name} · {len(cols)} columns"
            else:
                status = "local mode: views are created on Databricks"
        elif self.export_hook is not None:
            run = self.export_hook(uid, user.name)
            status = f"{now_iso()} · refresh started by {user.name}"
        else:
            status = "local mode: the mdm_export job runs on Databricks"
        cur = self.repo.get(uid) or n
        self.repo.upsert([dict(cur, properties={**(cur["properties"] or {}), "Last Run": status})], user.name)
        return {**run, "message": status}

