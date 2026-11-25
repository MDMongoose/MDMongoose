"""Configure Load of a Table node."""

from __future__ import annotations

from mdm import names
from mdm.hierarchy import RuleError, chain_of
from mdm.model import K, clone
from mdm.permissions import READER, User
from mdm.uid import to_uid as _uid


class ConfigureLoadService:
    def load_config(self, user: User, uid: str) -> dict:
        from mdm import load
        cat = self._cat()
        t = self._node(uid)
        if K.NT_TABLE not in t["types"]:
            raise RuleError(f"'{t['name']}' is not a Table")
        chain = chain_of(self.repo, t)
        level = self._level(user, chain)
        cfg = load.table_config(t)
        rt = cat.types.get(cfg["record_type"])
        parts = (cfg["source"] or "..").split(".")
        while len(parts) < 3:
            parts.insert(0, "")
        if not parts[1]:
            parts[1] = names.STAGING
        if not parts[0] and self.sources:
            cats = self.sources.catalogs()
            parts[0] = cats[0] if cats else ""
        cdf = self._has_cdf(cfg["source"]) if cfg["source"] else None
        return {
            "uid": uid, "name": t["name"], "path": " / ".join(c["name"] for c in chain[:-1]),
            "types": [cat.namer()(x) for x in t["types"]], "can_edit": level >= self._action_level(K.A_CONFIGURE_LOAD) and not t["locked"],
            "modified_at": t["modified_at"], "dataset": f"rec_{cfg['key']}",
            "catalog": parts[0], "schema": parts[1], "table": parts[2], "keys": cfg["keys"],
            "name_expression": cfg["name_expression"],
            "record_type": {"uid": rt["uid"], "name": rt["name"],
                            "props": [d["name"] for d in rt["properties"].get("property_defs", [])]} if rt else None,
            "record_types": [{"uid": x["uid"], "name": x["name"]} for x in cat.types.values()
                             if x["uid_num"] > 1000 or x["uid"] == K.NT_RECORD],
            "mapping": [{"source": a, "property": b, "transform": c or "—"} for a, b, c in cfg["mapping"]],
            "load_mode": "incremental" if cfg["load_mode"] == load.LOAD_INCREMENTAL else "full",
            "schedule": cfg["schedule"],
            "delete_mode": {load.DELETE_SOFT: "soft", load.DELETE_HARD: "hard", load.DELETE_KEEP: "keep"}.get(cfg["delete_mode"], "soft"),
            "record_icon": cfg["record_icon"] or "", "max_child_depth": t["max_child_depth"],
            "run_golden": cfg["run_golden"], "expectations": cfg["expectations"],
            "transforms": [k for k in load.TRANSFORMS if k not in ("", "none")],
            "cdf": cdf, "cdf_version": (t["properties"] or {}).get("_cdf_version"),
        }

    def _has_cdf(self, name: str) -> bool | None:
        f = getattr(self.sources, "has_cdf", None)
        return f(name) if f else None

    def source_cdf(self, user: User, name: str) -> dict:
        return {"table": name, "cdf": self._has_cdf(name)}

    def save_load_config(self, user: User, uid: str, body: dict) -> dict:
        from mdm import load
        cat = self._cat()
        t = self._node(uid)
        chain = chain_of(self.repo, t)
        self._need_action(user, chain, K.A_CONFIGURE_LOAD)
        self._not_locked(t)
        n = clone(t)
        p = n["properties"]
        name = ".".join(x for x in (body.get("catalog"), body.get("schema"), body.get("table")) if x)
        if not body.get("table"):
            raise RuleError("choose a source table")
        if name != p.get("Table Name") and self._has_cdf(name) is False:
            raise RuleError(f"{name} has no change data feed. Load the source into {names.STAGING} as a Delta table with "
                            f"CDF on: ALTER TABLE {name} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
        keys = [k for k in body.get("keys") or [] if k]
        if not keys:
            raise RuleError("choose at least one key column")
        p["Table Name"] = name
        p["Keys"] = keys
        p["Name Expression"] = body.get("name_expression") or ""
        rt = body.get("record_type")
        if isinstance(rt, dict):
            rt = rt.get("uid")
        if rt:
            p["Record Type"] = self._parse("Link(NodeType)", rt, cat.resolver())
            n["allowed_child_types"] = [p["Record Type"]]
        p["Column Mapping"] = [load.mapping_entry(m["source"], m.get("property") or m["source"], m.get("transform") or "")
                               for m in body.get("mapping") or [] if m.get("source")]
        p["Load Mode"] = load.LOAD_INCREMENTAL if body.get("load_mode") == "incremental" else _uid(1301)
        p["Schedule"] = (body.get("schedule") or "").strip()
        p["Delete Mode"] = {"soft": load.DELETE_SOFT, "hard": load.DELETE_HARD, "keep": load.DELETE_KEEP}[body.get("delete_mode") or "soft"]
        p["Record Icon"] = body.get("record_icon") or None
        p["Run Golden After Load"] = bool(body.get("run_golden"))
        if body.get("expectations") is not None:
            p["Expectations"] = [e for e in body["expectations"] if e.strip()]
        if body.get("max_child_depth") not in (None, ""):
            n["max_child_depth"] = max(1, int(body["max_child_depth"]))
        if p["Schedule"] and len(p["Schedule"].split()) != 5:
            raise RuleError("Schedule must be a 5-field cron expression, e.g. 0 2 * * *")
        self.repo.upsert([n], user.name, {uid: body["expected"]} if body.get("expected") else None)
        return {"saved": True, "dataset": f"rec_{load.dataset_key(n)}"}

    def source_catalog(self, user: User, catalog: str = "", schema: str = "") -> dict:
        if not self.sources:
            return {"catalogs": [], "schemas": [], "tables": []}
        return {"catalogs": self.sources.catalogs(),
                "schemas": self.sources.schemas(catalog) if catalog else [],
                "tables": self.sources.tables(catalog, schema) if catalog and schema else []}

    def load_preview(self, user: User, uid: str, body: dict) -> dict:
        from mdm import load
        t = self._node(uid)
        self._need(user, chain_of(self.repo, t), READER)
        if not self.sources:
            raise RuleError("no source reader configured")
        name = ".".join(x for x in (body.get("catalog"), body.get("schema"), body.get("table")) if x)
        cols, rows = self.sources.sample(name, 10)
        cfg = {"mapping": [(m["source"], m.get("property") or m["source"], m.get("transform") or "")
                           for m in body.get("mapping") or [] if m.get("source")],
               "name_expression": body.get("name_expression") or "", "keys": body.get("keys") or []}
        out = []
        for r in rows:
            data = {k: (None if v is None else str(v)) for k, v in r.items()}
            key = load.source_key(data, cfg["keys"]) if cfg["keys"] else ""
            out.append({"key": key, "name": load.record_name(cfg, data, key), "properties": load.map_row(cfg, data)})
        return {"columns": cols, "raw": rows, "records": out}

