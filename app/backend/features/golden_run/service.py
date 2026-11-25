"""Golden records run: Load → Match → Review → Survivorship → Export of one Master Data node."""

from __future__ import annotations

from mdm.hierarchy import RuleError, chain_of
from mdm.model import K
from mdm.permissions import READER, User

from ..golden_setup.service import setup_kind

STEPS = ["load", "match", "survivorship", "export"]


class GoldenRunService:
    def _can(self, user: User, node: dict, action_uid: str) -> bool:
        return self._level(user, chain_of(self.repo, node)) >= self._action_level(action_uid)

    def _run_sources(self, mp: dict) -> list[str]:
        out = []
        for rule_prop in ("Match Rule", "Survivorship Rule"):
            r = self.repo.get(mp.get(rule_prop) or "")
            for u in ((r or {}).get("properties") or {}).get("Sources") or []:
                if u not in out:
                    out.append(u)
        return out

    def _exports_of(self, master_uid: str) -> list[dict]:
        return [e for e in self.repo.by_types([K.NT_EXPORT]) if (e["properties"] or {}).get("Root Node") == master_uid]

    def golden_run_view(self, user: User, uid: str) -> dict:
        from mdm import runlog
        from mdm.matching import run as mr
        from mdm.matching.spec import METHOD_LABELS, load_spec
        node = self._node(uid)
        self._need(user, chain_of(self.repo, node), READER)
        kind = setup_kind(node)
        master = self._master_of(node, kind)
        out = {"opened_from": kind, "node": {"uid": node["uid"], "name": node["name"]}, "master": None,
               "masters": self._masters(), "can_assign": False}
        if master is None:
            out["can_assign"] = any(self._can(user, m, K.A_EDIT) for m in self.repo.by_types([K.NT_MASTER_DATA]))
            return out
        mp = master["properties"] or {}
        name_of = self._cat().namer()
        sink = runlog.sink_for(self.repo)

        def last(node_uid, activity):
            try:
                rows = sink.read(node_uid=node_uid, activity=activity, limit=1)
            except Exception:  # noqa: BLE001 - no log table yet
                return None
            return rows[0] if rows else None

        tables = [t for t in (self.repo.get(u) for u in self._run_sources(mp)) if t]
        counts = self.repo.child_counts([t["uid"] for t in tables])
        labels = {t["uid"]: t["label"] for t in self._table_columns(tables)}
        from mdm import processing
        use = processing.mode_of(self.repo, master)
        out["master"] = {"uid": master["uid"], "name": master["name"], "processing": processing.LABELS[use],
                         "processing_note": processing.SPLINK_NOTE if processing.chosen(master) != use else None}
        out["tables"] = [{
            "uid": t["uid"], "label": labels[t["uid"]], "table_name": (t["properties"] or {}).get("Table Name") or "",
            "records": counts.get(t["uid"], 0), "load_mode": name_of((t["properties"] or {}).get("Load Mode")) or "",
            "schedule": (t["properties"] or {}).get("Schedule") or "", "last_load": last(t["uid"], "load"),
            "can_load": self._can(user, t, K.A_LOAD_CHILDREN) and bool((t["properties"] or {}).get("Table Name"))}
            for t in tables]
        rule = self.repo.get(mp.get("Match Rule") or "")
        if rule:
            spec = load_spec(self.repo, rule["uid"])
            q = mr.review_queue(self.repo, rule["uid"], limit=10)
            out["match"] = {"uid": rule["uid"], "name": rule["name"], "method": METHOD_LABELS[spec.method],
                            "needs_full": (rule["properties"] or {}).get("_config_hash") not in (None, spec.fingerprint()),
                            "last_run": (rule["properties"] or {}).get("Last Run"), "last": last(rule["uid"], "match"),
                            "link": spec.link_threshold, "review": spec.review_threshold,
                            "open": q["open"], "open_count": q["open_count"], "decided_count": q["decided_count"]}
        surv = self.repo.get(mp.get("Survivorship Rule") or "")
        if surv:
            out["survivorship"] = {"uid": surv["uid"], "name": surv["name"], "last": last(master["uid"], "golden"),
                                   "rules": sum(1 for c in self.repo.children(surv["uid"]) if K.NT_PROPERTY_RULE in c["types"])}
        out["exports"] = [{"uid": e["uid"], "name": e["name"], "type": name_of((e["properties"] or {}).get("Export Type")) or "",
                           "location": (e["properties"] or {}).get("Location") or "",
                           "last_run": (e["properties"] or {}).get("Last Run") or "",
                           "can_run": self._can(user, e, K.A_RUN_EXPORT)} for e in self._exports_of(master["uid"])]
        out["can"] = {"match": bool(rule) and self._can(user, master, K.A_RUN_MATCHING),
                      "survivorship": bool(surv) and self._can(user, master, K.A_REFRESH_GOLDEN),
                      "review": self._can(user, master, K.A_REVIEW_MATCHES)}
        return out

    def golden_run_steps(self, user: User, uid: str, body: dict) -> tuple[dict, list[dict]]:
        """The steps of a run: body.only (one step) or every step up to body.until (load | match | survivorship |
        export), permissions checked.
        A preview only previews matching: the other steps have nothing to preview."""
        node = self._node(uid)
        master = self._master_of(node, setup_kind(node))
        if master is None:
            raise RuleError("assign the rule to a Master Data node first")
        mp = master["properties"] or {}
        only = body.get("only")
        if only:   # just this step
            if only not in STEPS:
                raise RuleError(f"only must be one of {', '.join(STEPS)}")
            want = [only]
        else:
            until = body.get("until") or "export"
            if until not in STEPS:
                raise RuleError(f"until must be one of {', '.join(STEPS)}")
            want = STEPS[: STEPS.index(until) + 1]
        preview = bool(body.get("preview"))
        if preview:
            if "match" not in want:
                raise RuleError("loading has nothing to preview — a preview starts at Match")
            want = ["match"]
        steps = []
        if "load" in want:
            pick = set(body.get("tables") or [])
            for u in self._run_sources(mp):
                t = self.repo.get(u)
                if t and (not pick or u in pick):
                    self._need_action(user, chain_of(self.repo, t), K.A_LOAD_CHILDREN)
                    steps.append({"key": "load", "label": f"Load {t['name']}", "kind": "run",
                                  "handler": "pipeline:mdm_load", "uid": u, "options": {}})
        mchain = chain_of(self.repo, master)
        if "match" in want:
            if not mp.get("Match Rule"):
                raise RuleError(f"'{master['name']}' has no Match Rule")
            self._need_action(user, mchain, K.A_RUN_MATCHING)
            steps.append({"key": "match", "label": "Preview matching" if preview else "Match", "kind": "run",
                          "handler": "job:mdm_match", "uid": master["uid"],
                          "options": {"mode": body.get("mode") or "incremental", "preview": preview, "golden": False}})
        if "survivorship" in want:
            if not mp.get("Survivorship Rule"):
                raise RuleError(f"'{master['name']}' has no Survivorship Rule")
            self._need_action(user, mchain, K.A_REFRESH_GOLDEN)
            steps.append({"key": "survivorship", "label": "Survivorship", "kind": "run", "handler": "mdm.golden.refresh",
                          "uid": master["uid"], "options": {"retire": body.get("retire", True) is not False,
                                                            "scope": body.get("scope") or "changed"}})
        if "export" in want:
            from mdm import exports
            name_of = self._cat().namer()
            for e in self._exports_of(master["uid"]):
                self._need_action(user, chain_of(self.repo, e), K.A_RUN_EXPORT)
                steps.append({"key": "export", "label": f"Export {e['name']}", "kind": "export", "uid": e["uid"],
                              "export_kind": exports.spec_of(e, name_of)["kind"]})
        if not steps:
            raise RuleError("nothing to run")
        return master, steps
