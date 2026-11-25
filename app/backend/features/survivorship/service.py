"""Survivorship rule editor (Edit Rule)."""

from __future__ import annotations

from mdm.hierarchy import RuleError, chain_of
from mdm.model import K, clone, new_node
from mdm.permissions import READER, User
from mdm.uid import is_reserved

from ...core.service import (
    Forbidden,
    _custom_functions,
)


class SurvivorshipService:
    def rule_editor(self, user: User, uid: str) -> dict:
        from mdm import golden, load
        cat = self._cat()
        rule = self._node(uid)
        if K.NT_SURVIVORSHIP_RULE not in rule["types"]:
            raise RuleError(f"'{rule['name']}' is not a Survivorship Rule")
        level = self._level(user, chain_of(self.repo, rule))
        if level < READER:
            raise Forbidden("no access")
        rp = rule["properties"] or {}
        gtype = cat.types.get(rp.get("Golden Record Type"))
        sources = [s for s in (self.repo.get(u) for u in rp.get("Sources") or []) if s]
        parents = self.repo.get_many([s["parent_uid"] for s in sources])
        src = []
        for s in sources:
            rt = cat.types.get((s["properties"] or {}).get("Record Type"))
            cols = [d["name"] for d in (rt["properties"].get("property_defs", []) if rt else [])]
            cols += [m[1] for m in load.table_config(s)["mapping"] if m[1] not in cols]
            src.append({"uid": s["uid"], "name": s["name"],
                        "label": (parents[s["parent_uid"]]["name"] + " · " if s["parent_uid"] in parents else "") + s["name"],
                        "columns": cols + ["Loaded At"]})
        props = [d for d in (gtype["properties"].get("property_defs", []) if gtype else [])
                 if d["group"] != "Survivorship" and d["name"] not in ("Survivorship Rule", "Match Score")]
        existing = {c["property"]: c for c in (golden.rule_cfg(r) for r in self.repo.children(uid)
                                                if K.NT_PROPERTY_RULE in r["types"])}
        rules = []
        for d in props:
            c = existing.get(d["name"]) or {"uid": None, "property": d["name"], "strategy": "sp",
                                             "priority": [s["uid"] for s in sources], "mapping": {}, "recency": "Loaded At",
                                             "custom": "", "ignore_empty": True, "ud_wins": True, "eligible": "",
                                             "cleaned": False}
            c = dict(c, ptype=d["ptype"], group=d["group"])
            c["priority"] = [u for u in c["priority"] if u in {s["uid"] for s in sources}] + \
                [s["uid"] for s in sources if s["uid"] not in c["priority"]]
            rules.append(c)
        return {"uid": uid, "name": rule["name"], "modified_at": rule["modified_at"], "can_edit": level >= self._action_level(K.A_EDIT_RULE) and not rule["locked"],
                "golden_type": {"uid": gtype["uid"], "name": gtype["name"]} if gtype else None,
                "golden_types": [{"uid": t["uid"], "name": t["name"]} for t in cat.types.values()
                                 if not is_reserved(t["uid_num"]) or t["uid_num"] >= 1000],
                "sources": src, "tables": [{"uid": t["uid"], "name": t["name"]} for t in self.repo.by_types([K.NT_TABLE])],
                "match_rule": self._master_match_rule(uid),
                "rules": rules, "custom_functions": _custom_functions()}

    def _master_match_rule(self, survivorship_uid: str) -> dict | None:
        for m in self.repo.by_types([K.NT_MASTER_DATA]):
            if (m["properties"] or {}).get("Survivorship Rule") == survivorship_uid:
                r = self.repo.get((m["properties"] or {}).get("Match Rule") or "")
                if r:
                    return {"uid": r["uid"], "name": r["name"], "master": m["name"]}
        return None

    def save_rule(self, user: User, uid: str, body: dict) -> dict:
        from mdm import golden
        cat = self._cat()
        rule = self._node(uid)
        chain = chain_of(self.repo, rule)
        self._need_action(user, chain, K.A_EDIT_RULE)
        self._not_locked(rule)
        resolve = cat.resolver()
        r = clone(rule)
        if body.get("name"):
            r["name"] = body["name"].strip()
        if body.get("golden_type"):
            r["properties"]["Golden Record Type"] = self._parse("Link(NodeType)", body["golden_type"], resolve)
        if body.get("sources") is not None:
            r["properties"]["Sources"] = self._parse("Array(Table)", body["sources"], resolve)
        writes = [r]
        kids = {c["properties"].get("Property"): c for c in self.repo.children(uid) if K.NT_PROPERTY_RULE in c["types"]}
        new_cfgs = [c for c in body.get("rules", []) if c.get("property") not in kids]
        nums = iter(self.repo.allocate(len(new_cfgs)) if new_cfgs else [])
        for i, c in enumerate(body.get("rules", [])):
            if c.get("eligible"):
                golden.compile_condition(c["eligible"])  # raises RuleError on unsupported SQL
            if c.get("strategy") == "custom" and not (c.get("custom") or "").startswith("mdm_custom."):
                raise RuleError(f"{c['property']}: the Custom function must be in mdm_custom")
            node = kids.get(c["property"])
            if node is None:
                node = new_node(next(nums), c["property"], [K.NT_PROPERTY_RULE, K.NT_ORDERED], rule, max_child_depth=0)
            n = clone(node)
            n["sort_order"] = float(i + 1)
            n["properties"].update({
                "Property": c["property"], "Strategy": golden.STRATEGY_BY_KEY.get(c.get("strategy", "sp")),
                "Source Priority": [u for u in c.get("priority", []) if u],
                "Source Mapping": golden.mapping_text(c.get("mapping") or {}),
                "Recency Column": c.get("recency") or "Loaded At", "Custom Function": c.get("custom") or "",
                "Ignore Empty": bool(c.get("ignore_empty", True)), "UserDefined Wins": bool(c.get("ud_wins", True)),
                "Eligible If": c.get("eligible") or "", "Use Cleaned Value": bool(c.get("cleaned"))})
            writes.append(n)
        self.repo.upsert(writes, user.name, {uid: body["expected"]} if body.get("expected") else None)
        return {"saved": len(writes)}

    def rule_preview(self, user: User, uid: str, body: dict) -> dict:
        from mdm import golden
        self._need(user, chain_of(self.repo, self._node(uid)), READER)
        return golden.preview(self.repo, self.catalog, uid, body.get("property"), self._overrides(body),
                              body.get("record"))

    def rule_simulate(self, user: User, uid: str, body: dict) -> dict:
        from mdm import golden
        self._need(user, chain_of(self.repo, self._node(uid)), READER)
        return golden.simulate(self.repo, self.catalog, uid, self._overrides(body), int(body.get("limit") or 100))

    def _overrides(self, body: dict) -> dict:
        ov = {"rules": [], "rule": {}}
        for c in body.get("rules") or []:
            ov["rules"].append({k: c.get(k) for k in ("property", "strategy", "priority", "mapping", "recency",
                                                      "custom", "ignore_empty", "ud_wins", "eligible", "cleaned", "cleaning") if k in c})
        if body.get("sources"):
            ov["rule"]["Sources"] = body["sources"]
        return ov

