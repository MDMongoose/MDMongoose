"""Matching: the Match Rule editor, previews, the review queue and steward decisions."""

from __future__ import annotations

import json

from mdm.hierarchy import RuleError, chain_of
from mdm.matching import compare
from mdm.matching import run as mr
from mdm.matching.spec import (
    COMPARISON_BY_NAME,
    COMPARISON_NAMES,
    METHOD_BY_KEY,
    METHOD_LABELS,
    ROLE_BY_KEY,
    ROLE_LABELS,
    country_lookup,
    load_spec,
    masters_for_rule,
    parse_condition,
    standardization_tables,
)
from mdm.matching.filters import filters_list
from mdm.matching.standardise import STEP_HELP, STEP_NAMES, clean_values
from mdm.model import K, clone, new_node
from mdm.permissions import READER


class MatchingService:
    # ------------------------------------------------------------------ helpers
    def _match_rule_of(self, node: dict) -> dict:
        """The Match Rule of a Match Rule, Master Data, source record or golden record node."""
        if K.NT_MATCH_RULE in node["types"]:
            return node
        if K.NT_MASTER_DATA in node["types"]:
            r = self.repo.get((node["properties"] or {}).get("Match Rule") or "")
            if not r:
                raise RuleError(f"'{node['name']}' has no Match Rule — set one on the Master Data node")
            return r
        if node.get("origin") == "generated":
            parent = self.repo.get(node.get("parent_uid") or "")
            if parent and K.NT_MASTER_DATA in parent["types"]:
                return self._match_rule_of(parent)
        r = mr.rule_for_record(self.repo, node)
        if not r:
            raise RuleError(f"'{node['name']}' is not a source of any Match Rule")
        return r

    def _check_on(self, user, uid: str, action_uid: str | None, level: int = READER) -> tuple[dict, dict]:
        node = self._node(uid)
        chain = chain_of(self.repo, node)
        if action_uid:
            self._need_action(user, chain, action_uid)
        else:
            self._need(user, chain, level)
        return node, self._match_rule_of(node)

    # ------------------------------------------------------------------ editor
    def match_rule_editor(self, user, uid: str) -> dict:
        from mdm import load
        node, rule = self._check_on(user, uid, None)
        cat = self._cat()
        spec = load_spec(self.repo, rule["uid"])
        chain = chain_of(self.repo, rule)
        level = self._level(user, chain)
        src = []
        parents = self.repo.get_many([s["parent_uid"] for s in spec.sources if s.get("parent_uid")])
        for s in spec.sources:
            rt = cat.types.get((s["properties"] or {}).get("Record Type"))
            cols = [d["name"] for d in (rt["properties"].get("property_defs", []) if rt else [])]
            cols += [m[1] for m in load.table_config(s)["mapping"] if m[1] not in cols]
            src.append({"uid": s["uid"], "name": s["name"],
                        "label": (parents[s["parent_uid"]]["name"] + " · " if s.get("parent_uid") in parents else "") + s["name"],
                        "columns": cols})
        rp = rule["properties"] or {}
        model = spec.model
        draft = rp.get("_model_draft")
        draft = json.loads(draft) if isinstance(draft, str) and draft else None
        q = mr.review_queue(self.repo, rule["uid"], limit=500)
        from mdm.matching import splink_model
        return {
            "uid": rule["uid"], "name": rule["name"], "modified_at": rule["modified_at"],
            "can_edit": level >= self._action_level(K.A_EDIT_MATCH_RULE) and not rule["locked"],
            "can_train": level >= self._action_level(K.A_TRAIN_MATCHING),
            "method": spec.method, "sources": src,
            "tables": [{"uid": t["uid"], "name": t["name"]} for t in self.repo.by_types([K.NT_TABLE])],
            "fields": [f.to_dict() for f in spec.fields],
            "fuzzy_rules": [r.to_dict() for r in spec.fuzzy_rules], "blocking": spec.blocking, "filters": spec.filters,
            "link_threshold": spec.link_threshold, "review_threshold": spec.review_threshold,
            "model": _model_view(model, splink_model) if model else None,
            "draft": _model_view(draft, splink_model) if draft and (not model or draft.get("_version") != model.get("_version")) else None,
            "decisions": q["decided"], "open_count": q["open_count"],
            "sample_pairs": [{"a": r["a"]["uid"], "b": r["b"]["uid"], "a_label": r["a"]["label"], "b_label": r["b"]["label"],
                              "score": r["score"], "status": r["status"]} for r in (q["open"][:15] + q["decided"][:10])
                             if r["a"].get("label") != "(deleted)" and r["b"].get("label") != "(deleted)"],
            "last_run": rp.get("Last Run"), "needs_full": rp.get("_config_hash") not in (None, spec.fingerprint()),
            "masters": [{"uid": m["uid"], "name": m["name"]} for m in masters_for_rule(self.repo, rule["uid"])],
            "options": {
                "methods": [{"key": k, "label": v} for k, v in METHOD_LABELS.items()],
                "roles": [{"key": k, "label": v} for k, v in ROLE_LABELS.items()],
                "comparisons": [{"name": n, "levels": compare.describe(n)} for n in COMPARISON_NAMES],
                "cleaning": [{"name": n, "help": STEP_HELP[n]} for n in STEP_NAMES],
                "std_tables": self._std_tables(),
                "word_lists": [{"uid": e["uid"], "name": e["name"],
                                "words": [c["name"] for c in self.repo.children(e["uid"], limit=12)]}
                               for e in sorted(self.repo.by_kind("Enum"), key=lambda e: e["name"].lower())],
            },
        }

    def _std_tables(self) -> list[dict]:
        """The Standardization Tables a Standardize step can use."""
        from mdm.matching.spec import STD_OVERRIDE
        out = []
        for t in self.repo.by_types([K.NT_STANDARDIZATION_TABLE]):
            p = t["properties"] or {}
            out.append({"uid": t["uid"], "name": t["name"], "columns": p.get("Standardization Columns") or [],
                        "key": p.get("Standardization Key") or "",
                        "default": p.get("Default Value") or "" if p.get("Default Method") == STD_OVERRIDE else None})
        return out

    def _match_overrides(self, body: dict) -> dict:
        ov = {}
        for k in ("method", "fields", "fuzzy_rules", "blocking", "link_threshold", "review_threshold", "sources", "filters"):
            if body.get(k) is not None:
                ov[k] = body[k]
        return ov

    def save_match_rule(self, user, uid: str, body: dict) -> dict:
        rule = self._node(uid)
        if K.NT_MATCH_RULE not in rule["types"]:
            raise RuleError(f"'{rule['name']}' is not a Match Rule")
        chain = chain_of(self.repo, rule)
        self._need_action(user, chain, K.A_EDIT_MATCH_RULE)
        self._not_locked(rule)
        ov = self._match_overrides(body)
        for r in ov.get("fuzzy_rules") or []:
            for c in r.get("conditions") or []:
                parse_condition(f"{c.get('field')} >= {c.get('link')}"
                                + (f" review {c['review']}" if c.get("review") not in (None, "") else ""))
        spec = load_spec(self.repo, uid, ov)
        mine = {s["uid"] for s in spec.sources}
        for other in self.repo.by_types([K.NT_MATCH_RULE]):
            if other["uid"] != uid and mine & set((other["properties"] or {}).get("Sources") or []):
                t = self.repo.get(next(iter(mine & set(other["properties"]["Sources"]))))
                raise RuleError(f"the table '{t['name'] if t else '?'}' is already a source of the Match Rule "
                                f"'{other['name']}' — a record belongs to one Match Rule")
        errs = spec.validate()
        if errs:
            raise RuleError("; ".join(errs))
        r = clone(rule)
        if (body.get("name") or "").strip():
            r["name"] = body["name"].strip()
        rp = r["properties"]
        rp.update({"Sources": [s["uid"] for s in spec.sources], "Method": METHOD_BY_KEY[spec.method],
                   "Blocking Rules": list(spec.blocking), "Link Threshold": spec.link_threshold,
                   "Review Threshold": spec.review_threshold, "Source Filters": filters_list(spec.filters)})
        writes = [r]
        kids = {c["uid"]: c for c in self.repo.children(uid)}
        keep = set()
        new_count = sum(1 for f in spec.fields if not f.uid or f.uid not in kids) + \
            sum(1 for x in spec.fuzzy_rules if not x.uid or x.uid not in kids)
        nums = iter(self.repo.allocate(new_count) if new_count else [])
        from mdm.matching.spec import mapping_text
        for i, f in enumerate(spec.fields):
            cur = kids.get(f.uid) if f.uid else None
            n = clone(cur) if cur else new_node(next(nums), f.name, [K.NT_MATCH_FIELD, K.NT_ORDERED], rule,
                                                 icon="hash", max_child_depth=0)
            n["name"], n["sort_order"] = f.name, float(i + 1)
            n["properties"].update({"Role": ROLE_BY_KEY[f.role], "Comparison": COMPARISON_BY_NAME[f.comparison],
                                    "Cleaning": list(f.cleaning), "Source Mapping": mapping_text(f.mapping)})
            keep.add(n["uid"])
            writes.append(n)
        for i, fr in enumerate(spec.fuzzy_rules):
            cur = kids.get(fr.uid) if fr.uid else None
            n = clone(cur) if cur else new_node(next(nums), fr.name, [K.NT_FUZZY_RULE, K.NT_ORDERED], rule,
                                                 icon="sliders", max_child_depth=0)
            n["name"], n["sort_order"] = fr.name, float(100 + i)
            n["properties"]["Conditions"] = [c.text() for c in fr.conditions]
            keep.add(n["uid"])
            writes.append(n)
        for c in kids.values():
            if (K.NT_MATCH_FIELD in c["types"] or K.NT_FUZZY_RULE in c["types"]) and c["uid"] not in keep:
                writes.append(dict(c, is_deleted=True))
        self.repo.upsert(writes, user.name, {uid: body["expected"]} if body.get("expected") else None)
        return {"saved": len(writes), "needs_full": True}

    def match_clean_preview(self, user, uid: str, body: dict) -> dict:
        """A few values of each source column of one Match Field, before and after cleaning."""
        node, rule = self._check_on(user, uid, None)
        spec = load_spec(self.repo, rule["uid"], self._match_overrides(body))
        f = body.get("field") or {}
        steps, mapping = list(f.get("cleaning") or []), {k.upper(): v for k, v in (f.get("mapping") or {}).items() if v}
        from mdm.matching.standardise import check_steps
        errs = check_steps(steps)
        std, std_errs = standardization_tables(self.repo, steps)
        if errs or std_errs:
            raise RuleError("; ".join(errs + std_errs))
        out = []
        for s in spec.sources:
            col = mapping.get(s["uid"])
            if not col:
                continue
            vals = []
            for r in self.repo.children(s["uid"], limit=200):
                v = (r["properties"] or {}).get(col)
                if v not in (None, "") and v not in vals:
                    vals.append(v)
                if len(vals) >= 5:
                    break
            cleaned = clean_values(steps, vals, spec.countries or (country_lookup(self.repo) if any(
                "country" in x.lower() for x in steps) else None), std)
            out.append({"source": s["name"], "column": col, "values": [{"raw": a, "clean": b} for a, b in zip(vals, cleaned)]})
        return {"samples": out}

    def match_test_pair(self, user, uid: str, body: dict) -> dict:
        """How the rule as edited (unsaved) treats one pair of records: cleaned values, rules, verdict."""
        node, rule = self._check_on(user, uid, None)
        a, b = (body.get("a") or "").upper(), (body.get("b") or "").upper()
        if not a or not b:
            raise RuleError("pick two records")
        return mr.details(self.repo, rule["uid"], a, b, self._match_overrides(body))

    def match_blocking(self, user, uid: str, body: dict) -> dict:
        node, rule = self._check_on(user, uid, None)
        return mr.blocking_stats(self.repo, rule["uid"], self._match_overrides(body))

    # ------------------------------------------------------------------ review / details / decisions
    def match_review(self, user, uid: str) -> dict:
        node, rule = self._check_on(user, uid, None)
        out = mr.review_queue(self.repo, rule["uid"])
        level = self._level(user, chain_of(self.repo, node))
        out["can_decide"] = level >= self._action_level(K.A_REVIEW_MATCHES)
        return out

    def match_details(self, user, uid: str, a: str, b: str) -> dict:
        node, rule = self._check_on(user, uid, None)
        return mr.details(self.repo, rule["uid"], a.upper(), b.upper())

    def record_matching(self, user, uid: str) -> dict:
        node = self._node(uid)
        self._need(user, chain_of(self.repo, node), READER)
        if node.get("origin") == "generated":
            rule = self._match_rule_of(node)
            spec = load_spec(self.repo, rule["uid"])
            members = mr.entity_members(self.repo, spec, uid)
            return {"rule": {"uid": rule["uid"], "name": rule["name"], "method": METHOD_LABELS[spec.method]},
                    "golden": uid, "members": [mr._rec_ref(spec, r) for r in members], "pairs": [], "is_golden": True}
        return mr.record_matching(self.repo, uid)

    def _decision_action(self, node: dict) -> str:
        if K.NT_MASTER_DATA in node["types"] or K.NT_MATCH_RULE in node["types"]:
            return K.A_REVIEW_MATCHES
        if node.get("origin") == "generated":
            return K.A_GOLDEN_MATCHING
        return K.A_RECORD_MATCHING

    def match_decide(self, user, uid: str, body: dict) -> dict:
        """body: {a, b, status: same|not|open, comment} or {pair, status, comment}; uid = the node the steward works on."""
        node = self._node(uid)
        node, rule = self._check_on(user, uid, self._decision_action(node))
        a, b = body.get("a"), body.get("b")
        score = None
        if body.get("pair"):
            p = self._node(body["pair"])
            if K.NT_MATCH_PAIR not in p["types"]:
                raise RuleError("not a Match Pair")
            pp = p["properties"] or {}
            a, b, score = pp.get("Record A"), pp.get("Record B"), pp.get("Score")
        if not a or not b:
            raise RuleError("pick two records")
        return mr.decide(self.repo, rule["uid"], a.upper(), b.upper(), body.get("status") or "same", user.name,
                         body.get("comment") or "", score)

    def match_split(self, user, uid: str, body: dict) -> dict:
        """Not this entity: the record (body.record, default uid) leaves its entity."""
        node = self._node(uid)
        node, rule = self._check_on(user, uid, self._decision_action(node))
        return mr.not_this_entity(self.repo, rule["uid"], (body.get("record") or uid).upper(), user.name,
                                  body.get("comment") or "")

    def match_merge(self, user, uid: str, body: dict) -> dict:
        """Merge with: golden record uid joins the entity of body.other (a golden record or a source record)."""
        node = self._node(uid)
        node, rule = self._check_on(user, uid, self._decision_action(node))
        other = self._node((body.get("other") or "").upper())
        mine = uid if node.get("origin") == "generated" else (node["properties"] or {}).get("Golden UID")
        theirs = other["uid"] if other.get("origin") == "generated" else (other["properties"] or {}).get("Golden UID")
        if not mine or not theirs:
            raise RuleError("both sides need a Golden UID — run matching first")
        return mr.merge_entities(self.repo, rule["uid"], mine, theirs, user.name, body.get("comment") or "")

    def match_candidates(self, user, uid: str, q: str = "") -> list[dict]:
        """Records / golden records to pick in Same entity as… / Merge with… (search by name or key)."""
        node, rule = self._check_on(user, uid, None)
        spec = load_spec(self.repo, rule["uid"])
        qq = (q or "").strip().lower()
        out = []
        golden = node.get("origin") == "generated"
        if golden:
            parent = self.repo.get(node["parent_uid"])
            for g in self.repo.children(parent["uid"], q=q, limit=30):
                if g["uid"] != node["uid"]:
                    out.append({"uid": g["uid"], "label": g["name"], "sub": g["uid"]})
            return out
        for s in spec.sources:
            for r in self.repo.children(s["uid"], q=q, limit=15):
                if r["uid"] != node["uid"] and (not qq or qq in r["name"].lower() or qq in (r.get("source_key") or "").lower()):
                    out.append({"uid": r["uid"], "label": mr.record_label(spec, r), "sub": r["name"],
                                "golden": (r["properties"] or {}).get("Golden UID")})
        return out[:30]

    def match_log(self, user, uid: str, limit: int = 20) -> list[dict]:
        from mdm import runlog
        node = self._node(uid)
        self._need(user, chain_of(self.repo, node), READER)
        uids = {uid}
        try:
            uids.add(self._match_rule_of(node)["uid"])
        except RuleError:
            pass
        rows = []
        sink = runlog.sink_for(self.repo)
        for u in uids:
            rows += sink.read(node_uid=u, limit=limit)
        return sorted(rows, key=lambda r: r.get("started_at") or "", reverse=True)[:limit]

    def match_activate(self, user, uid: str) -> dict:
        rule = self._node(uid)
        self._need_action(user, chain_of(self.repo, rule), K.A_TRAIN_MATCHING)
        return mr.activate_model(self.repo, uid, user.name)

    def match_rule_for(self, user, uid: str) -> dict:
        node = self._node(uid)
        self._need(user, chain_of(self.repo, node), READER)
        r = self._match_rule_of(node)
        return {"uid": r["uid"], "name": r["name"]}


def _model_view(model: dict, splink_model) -> dict:
    names = model.get("_fields") or {}
    comps = []
    for c in splink_model.level_weights(model):
        comps.append({"field": names.get(c["column"], c["column"]),
                      "levels": [{"label": lv["label"], "weight": None if lv["weight"] is None else round(lv["weight"], 2)}
                                 for lv in c["levels"] if not lv["null"]]})
    p = float(model.get("probability_two_random_records_match") or 1e-4)
    return {"version": model.get("_version"), "trained_at": model.get("_trained_at"), "trained_by": model.get("_trained_by"),
            "records": model.get("_records"), "accuracy": model.get("_accuracy"), "notes": model.get("_notes") or [],
            "prior": p, "prior_one_in": round(1 / p) if p else None, "comparisons": comps}


