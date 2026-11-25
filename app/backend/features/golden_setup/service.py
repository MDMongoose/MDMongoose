"""Golden record setup: the Match Rule and the Survivorship Rule of a Master Data node, edited together."""

from __future__ import annotations

from mdm.hierarchy import RuleError, chain_of
from mdm.model import K
from mdm.permissions import READER, User


def setup_kind(node: dict) -> str:
    if K.NT_MASTER_DATA in node["types"]:
        return "master"
    if K.NT_MATCH_RULE in node["types"]:
        return "match"
    if K.NT_SURVIVORSHIP_RULE in node["types"]:
        return "survivorship"
    raise RuleError(f"'{node['name']}' is not a Master Data node, a Match Rule or a Survivorship Rule")


class GoldenSetupService:
    def _master_of(self, node: dict, kind: str) -> dict | None:
        """The Master Data node itself, or the one the rule is assigned to."""
        if kind == "master":
            return node
        prop = "Match Rule" if kind == "match" else "Survivorship Rule"
        return next((m for m in self.repo.by_types([K.NT_MASTER_DATA])
                     if (m["properties"] or {}).get(prop) == node["uid"]), None)

    def _masters(self) -> list[dict]:
        return [{"uid": m["uid"], "name": m["name"],
                 "match_rule": (m["properties"] or {}).get("Match Rule"),
                 "survivorship_rule": (m["properties"] or {}).get("Survivorship Rule")}
                for m in self.repo.by_types([K.NT_MASTER_DATA])]

    def _table_columns(self, tables: list[dict]) -> list[dict]:
        from mdm import load
        cat = self._cat()
        parents = self.repo.get_many([t["parent_uid"] for t in tables if t.get("parent_uid")])
        out = []
        for t in tables:
            rt = cat.types.get((t["properties"] or {}).get("Record Type"))
            cols = [d["name"] for d in (rt["properties"].get("property_defs", []) if rt else [])]
            cols += [m[1] for m in load.table_config(t)["mapping"] if m[1] not in cols]
            out.append({"uid": t["uid"], "name": t["name"], "columns": cols,
                        "label": (parents[t["parent_uid"]]["name"] + " · " if t.get("parent_uid") in parents else "")
                        + t["name"]})
        return out

    def golden_setup(self, user: User, uid: str) -> dict:
        node = self._node(uid)
        self._need(user, chain_of(self.repo, node), READER)
        kind = setup_kind(node)
        master = self._master_of(node, kind)
        mp = (master or {}).get("properties") or {}
        match_uid = mp.get("Match Rule") if master else (uid if kind == "match" else None)
        surv_uid = mp.get("Survivorship Rule") if master else (uid if kind == "survivorship" else None)
        match = self.match_rule_editor(user, match_uid) if match_uid and self.repo.get(match_uid) else None
        surv = self.rule_editor(user, surv_uid) if surv_uid and self.repo.get(surv_uid) else None
        can_assign = False
        if master is None:
            masters = self.repo.by_types([K.NT_MASTER_DATA])
            can_assign = any(self._level(user, chain_of(self.repo, m)) >= self._action_level(K.A_EDIT) for m in masters)
        cat = self._cat()
        type_props = {t["uid"]: [{"name": d["name"], "ptype": d["ptype"], "group": d["group"]}
                                 for d in t["properties"].get("property_defs", [])
                                 if d["group"] != "Survivorship" and d["name"] not in ("Survivorship Rule", "Match Score")]
                      for t in cat.types.values() if t["uid"] in {g["uid"] for g in (surv or {}).get("golden_types", [])}}
        from mdm import processing
        proc = None
        if master:
            proc = {"mode": processing.chosen(master), "runs_as": processing.mode_of(self.repo, master),
                    "can_edit": self._level(user, chain_of(self.repo, master)) >= self._action_level(K.A_EDIT)}
        return {"opened_from": kind, "node": {"uid": node["uid"], "name": node["name"]},
                "type_props": type_props, "train_action": K.A_TRAIN_MATCHING, "processing": proc,
                "master": {"uid": master["uid"], "name": master["name"]} if master else None,
                "masters": self._masters(), "can_assign": can_assign,
                "match": match, "survivorship": surv,
                "tables": self._table_columns(self.repo.by_types([K.NT_TABLE]))}

    def golden_assign(self, user: User, uid: str, master_uid: str) -> dict:
        """Assign the rule `uid` to a Master Data node (its Match Rule or Survivorship Rule)."""
        node = self._node(uid)
        kind = setup_kind(node)
        if kind == "master":
            raise RuleError("pick a Match Rule or a Survivorship Rule to assign")
        master = self._node(master_uid)
        if K.NT_MASTER_DATA not in master["types"]:
            raise RuleError(f"'{master['name']}' is not a Master Data node")
        self.edit(user, master_uid, {"Match Rule" if kind == "match" else "Survivorship Rule": uid})
        return self.golden_setup(user, uid)

    def golden_processing(self, user: User, uid: str, mode: str) -> dict:
        """Processing of the Master Data node's golden records run: one_machine | distributed (mdm.processing)."""
        from mdm import processing
        node = self._node(uid)
        master = self._master_of(node, setup_kind(node))
        if master is None:
            raise RuleError("assign the rule to a Master Data node first")
        if mode not in ("one_machine", "distributed"):
            raise RuleError("processing must be one_machine or distributed")
        if mode == "distributed" and processing.match_method(self.repo, master) == "splink":
            raise RuleError(processing.SPLINK_NOTE + " — pick another matching strategy for Distributed processing")
        self.edit(user, master["uid"], {"Processing": processing.DISTRIBUTED if mode == "distributed"
                                        else processing.ONE_MACHINE})
        return {"mode": mode}
