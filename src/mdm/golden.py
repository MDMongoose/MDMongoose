"""Refresh Golden Records for a Master Data node.

1. The Master Data node's Survivorship Rule names a Golden Record Type, the
   source Tables and one Property Rule child per golden property.
2. Records (children of the source Tables) are mapped to golden values with
   each Property Rule's Source Mapping ("<tableUID>=<COLUMN>; ...").
3. Grouping: the records of one entity share a Golden UID, written on each
   record by matching (mdm.matching, the step before survivorship). Records
   without one are not matched yet and are skipped.
4. Survivorship, independently per property: drop ineligible candidates
   (Ignore Empty, Eligible If), keep a UserDefined value if UserDefined Wins,
   else pick by Strategy: Source Priority, Most Recent, or Custom function.
5. The golden record of an entity is the Master Data child whose UID is the
   Golden UID (created when missing); golden records without source records are retired.
"""

from __future__ import annotations

import importlib
import json
import re
from typing import Any, Callable

from .catalog import Catalog
from .formats import parse_value
from .hierarchy import RuleError
from .model import K, clone, new_node, now_iso
from .repo import NodeRepo
from .uid import from_uid, to_uid

STRATEGY_SOURCE_PRIORITY = to_uid(1321)
STRATEGY_MOST_RECENT = to_uid(1322)
STRATEGY_CUSTOM = to_uid(1323)

LEGAL_FORMS = (r"\b(?:s\.\s?r\.\s?o|a\.\s?s|s\.\s?r\.\s?l|s\.\s?a|b\.\s?v|n\.\s?v)\.?(?=\W|$)"
               r"|\b(gmbh|ag|zrt|kft|nyrt|bt|ltd|limited|inc|corp|co|llc|sa|sarl|srl|sro|bv|nv|plc|se|kg)\b")


# --------------------------------------------------------------------------- Eligible If
def _like_to_regex(pattern: str) -> str:
    out = ""
    for ch in pattern:
        out += ".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
    return f"^{out}$"


_COND = re.compile(
    r"""^\s*(?:
      value\s+(?P<neg>NOT\s+)?(?P<op>RLIKE|REGEXP|LIKE)\s+'(?P<pat>(?:[^']|'')*)'
    | value\s+IS\s+(?P<nn>NOT\s+)?NULL
    | value\s*(?P<cmp>=|<>|!=)\s*'(?P<lit>(?:[^']|'')*)'
    | length\(\s*value\s*\)\s*(?P<lop>>=|<=|>|<|=)\s*(?P<num>\d+)
    | source\s*(?P<scmp>=|<>|!=)\s*'(?P<slit>(?:[^']|'')*)'
    )\s*$""", re.I | re.X)


def compile_condition(expr: str | None) -> Callable[..., bool]:
    """A safe subset of SQL predicates over `value` and `source`, joined with AND / OR (AND binds
    tighter): value [NOT] RLIKE|LIKE '...', value IS [NOT] NULL, value = | <> '...',
    length(value) op n, source = | <> '...' (matches the source table's name, case-insensitive).
    The returned function takes (value, source=None)."""
    if not expr or not expr.strip():
        return lambda v, src=None: True
    ors = []
    for part in re.split(r"\s+OR\s+", expr.strip(), flags=re.I):
        ands = []
        for cond in re.split(r"\s+AND\s+", part, flags=re.I):
            m = _COND.match(cond)
            if not m:
                raise RuleError(f"unsupported condition: {cond.strip()!r}")
            ands.append(_one(m))
        ors.append(ands)
    return lambda v, src=None: any(all(f(v, src) for f in ands) for ands in ors)


def _one(m: re.Match) -> Callable[..., bool]:
    f = _one_value(m)
    if m.group("scmp"):
        lit, eq = m.group("slit").replace("''", "'").lower(), m.group("scmp") == "="
        return lambda v, src=None: ((lit in (src or "").lower()) == eq)
    return lambda v, src=None: f(v)


def _one_value(m: re.Match) -> Callable[[Any], bool] | None:
    if m.group("scmp"):
        return None
    if m.group("op"):
        pat = m.group("pat").replace("''", "'")
        rx = re.compile(pat if m.group("op").upper() != "LIKE" else _like_to_regex(pat), re.S)
        hit = (lambda v: v is not None and rx.search(str(v)) is not None) if m.group("op").upper() != "LIKE" \
            else (lambda v: v is not None and rx.match(str(v)) is not None)
        return (lambda v: v is not None and not hit(v)) if m.group("neg") else hit
    if m.group("cmp"):
        lit = m.group("lit").replace("''", "'")
        return (lambda v: v is not None and str(v) == lit) if m.group("cmp") == "=" \
            else (lambda v: v is not None and str(v) != lit)
    if m.group("lop"):
        n, op = int(m.group("num")), m.group("lop")
        ops = {">": lambda a: a > n, "<": lambda a: a < n, ">=": lambda a: a >= n, "<=": lambda a: a <= n,
               "=": lambda a: a == n}
        return lambda v: v is not None and ops[op](len(str(v)))
    return (lambda v: v not in (None, "")) if m.group("nn") else (lambda v: v in (None, ""))


# --------------------------------------------------------------------------- helpers
def parse_mapping(text: str | None) -> dict[str, str]:
    out = {}
    for part in (text or "").split(";"):
        if "=" in part:
            t, col = part.split("=", 1)
            out[t.strip().upper()] = col.strip()
    return out


def norm_name(s: Any) -> str:
    s = re.sub(LEGAL_FORMS, " ", str(s or "").lower())
    return re.sub(r"[^\w]+", " ", s).strip()


def norm_key(s: Any) -> str:
    return re.sub(r"[\s\-.]", "", str(s or "")).upper()


def load_function(path: str):
    mod, _, fn = path.rpartition(".")
    if not mod.startswith("mdm_custom"):
        raise RuleError("custom functions must live in the mdm_custom package")
    return getattr(importlib.import_module(mod), fn)


def _float(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- rule context
MANUAL = "__manual__"
STRATEGY_KEYS = {STRATEGY_SOURCE_PRIORITY: "sp", STRATEGY_MOST_RECENT: "mr", STRATEGY_CUSTOM: "custom"}
STRATEGY_BY_KEY = {v: k for k, v in STRATEGY_KEYS.items()}


def rule_cfg(pr: dict) -> dict:
    """A Property Rule node -> the plain config the UI edits and the engine runs."""
    p = pr["properties"] or {}
    return {
        "uid": pr["uid"], "property": p.get("Property"),
        "strategy": STRATEGY_KEYS.get(p.get("Strategy"), "sp"),
        "priority": [u.upper() for u in (p.get("Source Priority") or [])],
        "mapping": parse_mapping(p.get("Source Mapping")),
        "recency": p.get("Recency Column") or "Loaded At",
        "custom": p.get("Custom Function") or "",
        "ignore_empty": p.get("Ignore Empty", True) is not False,
        "ud_wins": p.get("UserDefined Wins", True) is not False,
        "eligible": p.get("Eligible If") or "",
        "cleaned": p.get("Use Cleaned Value") is True,
    }


def mapping_text(mapping: dict[str, str]) -> str:
    return "; ".join(f"{t}={c}" for t, c in mapping.items() if c)


class RuleContext:
    """Everything a survivorship rule needs, loaded once: golden type, sources, records."""

    def __init__(self, repo: NodeRepo, catalog: Catalog, rule_uid: str, overrides: dict | None = None,
                 load_records: bool = True):
        """load_records=False: everything but the records (Distributed processing reads them with Spark and
        hands each worker its entities through use_records)."""
        self.repo, self.cat = repo, catalog.refresh(force=True)
        rule = repo.get(rule_uid)
        if not rule or K.NT_SURVIVORSHIP_RULE not in rule["types"]:
            raise RuleError(f"{rule_uid} is not a Survivorship Rule")
        self.rule = rule
        rp = dict(rule["properties"] or {})
        rp.update({k: v for k, v in (overrides or {}).get("rule", {}).items() if v is not None})
        self.rp = rp
        self.gtype = self.cat.types.get(rp.get("Golden Record Type"))
        if not self.gtype:
            raise RuleError("the rule has no valid Golden Record Type")
        self.gdefs = {d["name"]: d for d in self.gtype["properties"].get("property_defs", [])}
        self.sources = [s for s in (repo.get(u) for u in rp.get("Sources") or []) if s]
        if not self.sources:
            raise RuleError("the rule has no Sources")
        parents = repo.get_many(list({s["parent_uid"] for s in self.sources if s["parent_uid"]}))
        self.src_name = {s["uid"]: s["name"] for s in self.sources}
        self.src_label = {s["uid"]: (parents[s["parent_uid"]]["name"] + " · " if s["parent_uid"] in parents else "")
                          + s["name"] for s in self.sources}
        self.prule_nodes = [r for r in repo.children(rule["uid"]) if K.NT_PROPERTY_RULE in r["types"]]
        cfgs = {c["property"]: c for c in (rule_cfg(r) for r in self.prule_nodes) if c["property"]}
        for c in (overrides or {}).get("rules", []):
            cfgs[c["property"]] = {**cfgs.get(c["property"], {}), **c}
        self.cfgs = {p: c for p, c in cfgs.items() if p in self.gdefs}
        records = []
        if load_records:
            for s in self.sources:
                records += [r for r in repo.children(s["uid"]) if r["origin"] == "loaded" or K.NT_RECORD in r["types"]]
        self.cleaning, self.countries, self.tables, self._clean_memo = {}, None, {}, {}
        self.filters: dict[str, str] = {}
        spec = self._match_spec()
        if spec:  # records the Match Rule's record filter leaves out take no part; its cleaning, for cleaned values
            self.filters = dict(spec.filters)
            self.cleaning = {f.name: list(f.cleaning) for f in spec.fields}
            self.countries = spec.countries or None
            self.tables = spec.tables
        self.name_prop = "Legal Name" if "Legal Name" in self.cfgs else ("Name" if "Name" in self.cfgs else None)
        self.use_records(records)

    def use_records(self, records: list[dict]) -> None:
        """The source records taken into account: those that pass the Match Rule's record filter."""
        if self.filters:
            from .matching.filters import passes
            keep = passes(self.filters)
            records = [r for r in records if keep(r)]
        self.records = records
        self.vals = {r["uid"]: {p: self.value_of(r, c) for p, c in self.cfgs.items()} for r in self.records}

    def detach(self) -> "RuleContext":
        """A copy without the repository, for Spark workers: everything cleaning needs is looked up now."""
        import copy
        from .matching.spec import country_lookup, standardization_tables
        steps = [s for c in self.cfgs.values() if c.get("cleaned")
                 for s in (c.get("cleaning") if c.get("cleaning") is not None else self.cleaning.get(c["property"]) or [])]
        if self.countries is None and any("country" in s.lower() for s in steps):
            self.countries = country_lookup(self.repo)
        more = [s for s in steps if s.split(" ")[-1].upper() not in self.tables]
        if more:
            self.tables = {**self.tables, **standardization_tables(self.repo, more)[0]}
        out = copy.copy(self)
        out.repo = out.cat = None
        out.records, out.vals, out._clean_memo = [], {}, {}
        return out

    def _match_spec(self):
        from .matching.spec import load_spec
        for m in self.repo.by_types([K.NT_MASTER_DATA]):
            mp = m["properties"] or {}
            if mp.get("Survivorship Rule") == self.rule["uid"] and mp.get("Match Rule"):
                try:
                    return load_spec(self.repo, mp["Match Rule"])
                except RuleError:
                    return None
        return None

    def cleaned(self, cfg: dict, value):
        """The value after the property's cleaning steps (Use Cleaned Value), or the value itself."""
        steps = cfg.get("cleaning") if cfg.get("cleaning") is not None else self.cleaning.get(cfg["property"])
        if not cfg.get("cleaned") or not steps or value in (None, ""):
            return value
        from .matching.spec import country_lookup, standardization_tables
        from .matching.standardise import clean_values
        countries = self.countries
        if countries is None and any("country" in s.lower() for s in steps) and self.repo is not None:
            countries = self.countries = country_lookup(self.repo)
        more = [s for s in steps if s.split(" ")[-1].upper() not in self.tables]
        tables = {**self.tables, **standardization_tables(self.repo, more)[0]} if more and self.repo is not None \
            else self.tables
        memo = self._clean_memo.setdefault(json.dumps(steps), {})
        if value not in memo:  # first use of these steps: clean every value of the property in one go
            todo = list({v for vals in self.vals.values() if (v := vals.get(cfg["property"])) not in (None, "")}
                        - set(memo) | {value})
            memo.update(zip(todo, clean_values(steps, todo, countries, tables)))
        return memo[value]

    def value_of(self, rec: dict, cfg: dict):
        col = (cfg.get("mapping") or {}).get(rec["parent_uid"])
        if not col:
            return None
        v = rec["properties"].get(col)
        return None if v is None or str(v).strip() == "" else str(v).strip()

    def recency_of(self, rec: dict, cfg: dict) -> tuple[str, str]:
        """(sort key, column used) for Most Recent."""
        r = cfg.get("recency") or "Loaded At"
        if "=" in r:
            col = parse_mapping(r).get(rec["parent_uid"])
            return (str(rec["properties"].get(col) or "") if col else ""), (col or "")
        return str(rec["properties"].get(r) or rec.get("modified_at") or ""), r

    def record_label(self, rec: dict) -> str:
        return f"{self.src_name.get(rec['parent_uid'], '?')} · {rec.get('source_key') or rec['name']}"

    def record_title(self, rec: dict) -> str:
        v = self.vals.get(rec["uid"], {})
        return " · ".join(str(x) for x in (v.get(self.name_prop) if self.name_prop else rec["name"], v.get("Country"),
                                           v.get("City"), v.get("VAT ID")) if x)

    # -- grouping ---------------------------------------------------------------------
    def match(self, threshold: float | None = None) -> list[tuple[list[dict], float, list[str]]]:
        """Entities = records with the same Golden UID, written by matching (mdm.matching). Records
        without one are not matched yet and are left out (see `unmatched`). Returns
        [(members, score, notes)] with the golden UID in self.key_of[id(members)]."""
        groups: dict[str, list[dict]] = {}
        self.unmatched = []
        for r in self.records:
            k = (r["properties"] or {}).get("Golden UID")
            if k:
                groups.setdefault(str(k), []).append(r)
            else:
                self.unmatched.append(r)
        out = []
        self.key_of = {}
        for k, members in sorted(groups.items()):
            scores = [_float(m["properties"].get("Match Score")) for m in members]
            scores = [x for x in scores if x is not None]
            entry = (members, round(min(scores), 3) if scores else 1.0, [])
            self.key_of[id(members)] = k
            out.append(entry)
        return out

    # -- survivorship of one property -------------------------------------------------
    def survive(self, cfg: dict, members: list[dict], golden: dict | None) -> dict:
        """Returns {rows, winner (row|None), reason, value} — rows are the candidate table of the UI."""
        try:
            cond = compile_condition(cfg.get("eligible"))
            cond_error = None
        except RuleError as e:
            cond, cond_error = (lambda v, src=None: True), str(e)
        prop = cfg["property"]
        order = cfg.get("priority") or [s["uid"] for s in self.sources]
        rank = lambda src: order.index(src) if src in order else len(order)
        rows = []
        for m in members:
            v = self.vals[m["uid"]].get(prop) if self.cfgs.get(prop) is cfg else self.value_of(m, cfg)
            rec_key, rec_col = self.recency_of(m, cfg)
            rows.append({"source": m["parent_uid"], "source_label": self.src_label.get(m["parent_uid"], "?"),
                         "record": m["uid"], "record_label": self.record_label(m), "value": v,
                         "loaded_at": m["properties"].get("Loaded At"),
                         "changed_on": rec_key if rec_col and rec_col != "Loaded At" else None,
                         "recency": rec_key, "rank": rank(m["parent_uid"])})
        ud = bool(golden and (golden.get("user_defined") or {}).get(prop))
        if ud:
            gv = golden["properties"].get(prop)
            rows.append({"source": MANUAL, "source_label": "UserDefined (manual)", "record": None,
                         "record_label": "UserDefined", "value": None if gv is None else str(gv),
                         "loaded_at": golden.get("modified_at"), "changed_on": golden.get("modified_at"),
                         "recency": str(golden.get("modified_at") or ""), "rank": len(order) + 1, "manual": True})
        for r in rows:
            if cfg.get("ignore_empty", True) and r["value"] in (None, ""):
                r["eligible"], r["why"] = False, "empty"
            elif not r.get("manual") and not cond(r["value"], r["source_label"]):
                r["eligible"], r["why"] = False, "fails Eligible if"
            else:
                r["eligible"], r["why"] = True, ""
        el = sorted([r for r in rows if r["eligible"]], key=lambda r: r["rank"])
        win, reason = None, ""
        man = next((r for r in el if r.get("manual")), None)
        if cfg.get("ud_wins", True) and man:
            win, reason = man, "UserDefined (manual) value wins before the strategy runs"
        elif not el:
            reason = "no eligible candidate — the golden value stays empty"
        elif cfg.get("strategy") == "mr":
            best = sorted(el, key=lambda r: (r["recency"] or "", -r["rank"]), reverse=True)
            win = best[0]
            tie = len(best) > 1 and best[1]["recency"] == win["recency"]
            col = cfg.get("recency") or "Loaded At"
            reason = (f"Most Recent by {'changed-on' if '=' in col else col}: {win['recency'] or '—'}"
                      + (" — tie broken by the order" if tie else ""))
        elif cfg.get("strategy") == "custom":
            fn_path = cfg.get("custom") or ""
            try:
                fn = load_function(fn_path)
                pairs = [(r["source_label"].split(" · ")[-1], r["value"]) for r in el]
                picked = fn(pairs)
                win = next((r for r, pair in zip(el, pairs) if picked and tuple(picked) == pair), None)
                reason = (f"Custom: {fn_path}({', '.join(f'({a}, {b!r})' for a, b in pairs)}) returned "
                          + (f"({picked[0]}, {picked[1]!r})" if picked else "None"))
            except Exception as e:  # noqa: BLE001 - shown to the user
                reason = f"Custom function failed: {e}"
        else:
            win = el[0]
            reason = f"Source Priority: {win['source_label']} is the highest-ranked source with an eligible value"
        for r in rows:
            r["step"] = "★ winner" if r is win else (f"candidate #{r['rank'] + 1}" if r["eligible"] else "filtered out")
            r["winner"] = r is win
        if cond_error:
            reason += f" (Eligible If ignored: {cond_error})"
        rows.sort(key=lambda r: r["rank"])
        value = win["value"] if win else None
        if win and not win.get("manual"):
            value = self.cleaned(cfg, value)
            if value != win["value"]:
                reason += f" — cleaned: {win['value']!r} → {value!r}"
        return {"rows": rows, "winner": win, "reason": reason, "value": value,
                "source": win["source_label"] if win else None}

    def strategy_label(self, cfg: dict) -> str:
        s = {"sp": "Source Priority", "mr": "Most Recent", "custom": "Custom"}[cfg.get("strategy", "sp")]
        if cfg.get("strategy") == "sp":
            s += " (" + ", ".join(self.src_name.get(u, "?") for u in (cfg.get("priority") or [])) + ")"
        if cfg.get("eligible"):
            s += " + Eligible if"
        return s


# --------------------------------------------------------------------------- plan / refresh
def _fmt(v):
    if v is None or v == "":
        return "(empty)"
    return ", ".join(map(str, v)) if isinstance(v, list) else str(v)


def plan(repo: NodeRepo, catalog: Catalog, master_uid: str, threshold: float | None = None, retire: bool = True,
         overrides: dict | None = None) -> dict:
    """Dry run of Refresh Golden Records. Returns the UI plan plus the nodes to write."""
    master = repo.get(master_uid)
    if not master:
        raise RuleError(f"{master_uid} not found")
    rule_uid = (master["properties"] or {}).get("Survivorship Rule")
    if not rule_uid:
        raise RuleError("the Master Data node has no Survivorship Rule")
    ctx = RuleContext(repo, catalog, rule_uid, overrides)
    if not ctx.cfgs:
        raise RuleError("the rule has no Property Rules for the Golden Record Type's properties")
    cat = ctx.cat
    name_of = cat.namer()
    resolve = cat.resolver()
    clusters = ctx.match(threshold)
    existing = [g for g in repo.children(master["uid"]) if g["origin"] == "generated"]
    ex_by_uid = {g["uid"]: g for g in existing}
    used: set[str] = set()
    stamp = now_iso()
    groups, writes, conflicts = [], [], []
    link_members: dict[str, list[dict]] = {}
    changed_uids: set[str] = set()
    taken = repo.get_many([ctx.key_of[id(c[0])] for c in clusters if ctx.key_of[id(c[0])] not in ex_by_uid],
                          include_deleted=True)
    for members, score, notes in clusters:
        target = ctx.key_of[id(members)]
        one = build_golden(ctx, master, members, score, notes, target, ex_by_uid.get(target), taken.get(target),
                           stamp, resolve, name_of)
        if one["conflict"]:
            conflicts.append(one["conflict"])
            continue
        used.add(target)
        conflicts += one["conflicts"]
        g = one["golden"]
        link_members[g["uid"]] = one["link_members"]
        if one["write"]:
            changed_uids.add(g["uid"])
        writes.append(g)
        groups.append(one["group"])
    retired = []
    for g in existing:
        if g["uid"] in used:
            continue
        if retire and not g["locked"]:
            retired.append(dict(g, is_deleted=True))
            groups.append({"uid": g["uid"], "name": g["name"], "action": "Retire", "changes": "no source records left",
                           "score": "—", "notes": [], "records": [], "values": []})
        else:
            groups.append({"uid": g["uid"], "name": g["name"], "action": "Keep (orphan)", "changes": "no sources left",
                           "score": "—", "notes": [], "records": [], "values": []})
    order = {"Update": 0, "Create": 1, "Unchanged": 2, "Retire": 3, "Keep (orphan)": 4}
    groups.sort(key=lambda x: (order[x["action"]], x["name"]))
    counts: dict[str, int] = {}
    for x in groups:
        counts[x["action"]] = counts.get(x["action"], 0) + 1
    by_src: dict[str, int] = {}
    for r in ctx.records:
        by_src[ctx.src_name[r["parent_uid"]]] = by_src.get(ctx.src_name[r["parent_uid"]], 0) + 1
    gp = [d for d in ctx.gtype["properties"].get("property_defs", []) if d["name"] in ctx.cfgs]
    return {
        "master": {"uid": master["uid"], "name": master["name"], "types": [name_of(t) for t in master["types"]]},
        "rule": {"uid": ctx.rule["uid"], "name": ctx.rule["name"], "sources": [ctx.src_name[s["uid"]] for s in ctx.sources],
                 "property_rules": len(ctx.cfgs), "name_prop": ctx.name_prop},
        "match_rule": _match_rule_ref(repo, master),
        "unmatched": len(ctx.unmatched),
        "golden_type": {"uid": ctx.gtype["uid"], "name": ctx.gtype["name"], "props": len(gp)},
        "groups": groups, "counts": counts,
        "sources_summary": " + ".join(f"{n} {k}" for k, n in by_src.items()) + " records",
        "conflicts": conflicts,
        "_writes": writes, "_retired": retired, "_new": [], "_link_members": link_members, "_changed": changed_uids,
    }


def build_golden(ctx: RuleContext, master: dict, members: list[dict], score: float, notes: list, target: str,
                 old: dict | None, other: dict | None, stamp: str, resolve, name_of) -> dict:
    """The golden record of one entity (Refresh Golden Records). Entities are independent of each other, so
    One Machine processing calls this in a loop and Distributed processing on the Spark workers.
    old    the existing golden record (a generated child of the Master Data node with UID `target`)
    other  any other node with that UID, also a deleted one (None when there is none or `old` is set)
    Returns {conflict, conflicts, golden, write, link_members, group}."""
    if old is None and other is not None and not (other.get("is_deleted") and other.get("parent_uid") == master["uid"]):
        return {"conflict": f"Golden UID {target} is used by another node ({other['name']}) — re-run matching"}
    conflicts = []
    if old is None and other is not None:  # a retired golden record comes back
        old = dict(other, is_deleted=False)
    if old is None:
        g = new_node(from_uid(target), "", [ctx.gtype["uid"]], master, origin="generated", max_child_depth=0)
    else:
        g = clone(old)
    before = dict(g["properties"])
    vals_out, lineage = [], {}
    for prop, cfg in ctx.cfgs.items():
        res = ctx.survive(cfg, members, old)
        rule_txt = ctx.strategy_label(cfg)
        if res["winner"] and res["winner"].get("manual"):
            lineage[prop] = "UserDefined"
            vals_out.append({"prop": prop, "value": _fmt(g["properties"].get(prop)), "source": "UserDefined",
                             "rule": "UserDefined wins"})
            continue
        if res["value"] is None:
            g["properties"].pop(prop, None)
            vals_out.append({"prop": prop, "value": "", "source": "—", "rule": "no eligible value"})
            continue
        parsed = parse_value(ctx.gdefs[prop]["ptype"], res["value"], resolve)
        if not parsed.ok:
            conflicts.append(f"{prop}: {res['value']!r} ({parsed.error})")
            vals_out.append({"prop": prop, "value": res["value"], "source": res["source"],
                             "rule": f"not stored: {parsed.error}"})
            continue
        g["properties"][prop] = parsed.value
        lineage[prop] = res["source"]
        shown = name_of(parsed.value) if parse_ptype_base(ctx.gdefs[prop]["ptype"]) in ("Enum", "Link") else parsed.value
        vals_out.append({"prop": prop, "value": _fmt(shown), "source": res["source"].split(" · ")[-1], "rule": rule_txt})
    if "Survivorship Rule" in ctx.gdefs:
        g["properties"]["Survivorship Rule"] = ctx.rule["uid"]
    if "Match Score" in ctx.gdefs:
        g["properties"]["Match Score"] = score
    g["properties"]["_members"] = sorted(m["uid"] for m in members)
    g["properties"]["_lineage"] = lineage
    ud = g.get("user_defined") or {}
    if not ud.get("Name"):
        nm = g["properties"].get(ctx.name_prop) if ctx.name_prop else None
        g["name"] = str(nm or g["name"] or members[0]["name"])
    g["child_links"] = []   # the source records are Child Link nodes below the golden record (refresh writes them)
    changed = [k for k in set(before) | set(g["properties"])
               if not k.startswith("_") and before.get(k) != g["properties"].get(k) and k != "Match Score"]
    links_changed = old is not None and sorted(before.get("_members") or []) != g["properties"]["_members"]
    if old is None or other is not None and old.get("uid") == other.get("uid") and other.get("is_deleted"):
        action, change = "Create", "new golden record" if old is None else "restored golden record"
    elif changed or links_changed:
        action = "Update"
        parts = [f"{k}: {_fmt(name_of(before.get(k)) if isinstance(before.get(k), str) and before.get(k, '').startswith('MDM-') else before.get(k))} → "
                 f"{_fmt(name_of(g['properties'].get(k)) if isinstance(g['properties'].get(k), str) and str(g['properties'].get(k)).startswith('MDM-') else g['properties'].get(k))}"
                 for k in sorted(changed)]
        if links_changed:
            parts.append("source links changed")
        change = "; ".join(parts)
    else:
        action, change = "Unchanged", "—"
    stale = (before.get("Match Score") != g["properties"].get("Match Score")
             or bool(old and old.get("child_links")) or bool(old and old.get("name") != g["name"]))
    write = action != "Unchanged" or stale   # unchanged golden records are not rewritten: the views stay incremental
    if write:
        g["properties"]["_refreshed_at"] = stamp
    return {"conflict": None, "conflicts": conflicts, "golden": g, "write": write, "action": action,
            "link_members": sorted(members, key=lambda m: (m["parent_uid"], m["uid"])),
            "group": {"uid": g["uid"] if old else None, "name": g["name"], "action": action, "changes": change,
                      "score": f"{score:.2f}", "notes": notes,
                      "records": [{"uid": m["uid"], "label": ctx.record_label(m), "title": ctx.record_title(m)}
                                  for m in members],
                      "values": vals_out}}


def _match_rule_ref(repo, master: dict) -> dict | None:
    r = repo.get((master["properties"] or {}).get("Match Rule") or "")
    return {"uid": r["uid"], "name": r["name"], "last_run": (r["properties"] or {}).get("Last Run")} if r else None


def parse_ptype_base(ptype: str) -> str:
    return ptype.split("(", 1)[0].strip()


def refresh(repo: NodeRepo, catalog: Catalog, master_uid: str, user: str = "mdm_golden",
            threshold: float | None = None, retire: bool = True) -> dict:
    p = plan(repo, catalog, master_uid, threshold, retire)
    repo.upsert([g for g in p["_writes"] if g["uid"] in p["_changed"]] + p["_retired"], user)
    source_links(repo, p["_writes"], p["_retired"], p["_link_members"], user)
    c = p["counts"]
    return {"master": p["master"]["name"], "records": sum(len(x["records"]) for x in p["groups"]),
            "golden": len(p["_writes"]), "created": c.get("Create", 0),
            "updated": c.get("Update", 0) + c.get("Unchanged", 0), "changed": c.get("Update", 0),
            "retired": c.get("Retire", 0), "unmatched": p["unmatched"], "conflicts": p["conflicts"]}


def source_links(repo: NodeRepo, goldens: list[dict], retired: list[dict], members: dict[str, list[dict]],
                 user: str = "mdm_golden") -> int:
    """Each golden record has one Child Link node (Link Type "source") per source record it was built from:
    missing ones are created, links to records that left the cluster are deleted. One read for all links."""
    from .links import new_link, target_of
    from .uid import to_uid
    source = to_uid(1333)   # LinkTypes · source
    have: dict[str, dict[str, dict]] = {}
    for n in repo.links_under([g["uid"] for g in goldens + retired]):
        have.setdefault(n["parent_uid"], {})[target_of(n)] = n
    todo, gone = [], []
    for g in goldens:
        want = members.get(g["uid"], [])
        cur = have.get(g["uid"], {})
        for i, m in enumerate(want):
            if m["uid"] not in cur:
                todo.append((g, m, float(i + 1)))
        keep = {m["uid"] for m in want}
        gone += [dict(n, is_deleted=True) for t, n in cur.items() if t not in keep]
    for g in retired:
        gone += [dict(n, is_deleted=True) for n in have.get(g["uid"], {}).values()]
    nums = repo.allocate(len(todo)) if todo else []
    new = []
    for (g, m, order), num in zip(todo, nums):
        n = new_link(num, g, m, source, node_origin="generated", locked=True, max_child_depth=0)
        n["sort_order"] = order
        new.append(n)
    for i in range(0, len(new) + len(gone), 5000):
        repo.upsert((new + gone)[i:i + 5000], user)
    return len(new) + len(gone)


def public(p: dict) -> dict:
    return {k: v for k, v in p.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- rule editor support
def preview(repo: NodeRepo, catalog: Catalog, rule_uid: str, prop: str, overrides: dict | None = None,
            record_uid: str | None = None) -> dict:
    """Candidates and winner of one property for one matched cluster (largest one by default)."""
    ctx = RuleContext(repo, catalog, rule_uid, overrides)
    cfg = ctx.cfgs.get(prop)
    if not cfg:
        raise RuleError(f"no Property Rule for {prop}")
    clusters = sorted(ctx.match(), key=lambda c: -len(c[0]))
    if not clusters:
        return {"cluster": None, "rows": [], "reason": "no source records loaded yet", "value": None}
    pick = clusters[0]
    if record_uid:
        pick = next((c for c in clusters if any(m["uid"] == record_uid for m in c[0])), pick)
    members = pick[0]
    golden = _golden_for(repo, ctx, members)
    res = ctx.survive(cfg, members, golden)
    name = (golden or {}).get("name") or (ctx.vals[members[0]["uid"]].get(ctx.name_prop) if ctx.name_prop else members[0]["name"])
    return {"cluster": {"name": name, "records": len(members),
                        "options": [{"record": c[0][0]["uid"], "label": f"{(ctx.vals[c[0][0]['uid']].get(ctx.name_prop) if ctx.name_prop else c[0][0]['name']) or c[0][0]['name']} ({len(c[0])})"}
                                    for c in clusters[:50]]},
            "rows": [{k: v for k, v in r.items() if k not in ("recency",)} for r in res["rows"]],
            "reason": res["reason"], "value": res["value"], "source": res["source"]}


def _golden_for(repo, ctx, members):
    ids = {m["uid"] for m in members}
    for mnode in repo.by_types([K.NT_MASTER_DATA]):
        if (mnode["properties"] or {}).get("Survivorship Rule") != ctx.rule["uid"]:
            continue
        for g in repo.children(mnode["uid"]):
            if ids & set(g["properties"].get("_members") or []):
                return g
    return None


def simulate(repo: NodeRepo, catalog: Catalog, rule_uid: str, overrides: dict | None = None, limit: int = 100) -> dict:
    """Run every Property Rule over (up to) `limit` source records; summary per property."""
    ctx = RuleContext(repo, catalog, rule_uid, overrides)
    ctx.records = ctx.records[:limit]
    clusters = ctx.match()
    out = []
    for prop, cfg in ctx.cfgs.items():
        wins: dict[str, int] = {}
        empty = 0
        for members, _, _ in clusters:
            res = ctx.survive(cfg, members, _golden_for(repo, ctx, members))
            if res["winner"]:
                wins[res["source"]] = wins.get(res["source"], 0) + 1
            else:
                empty += 1
        out.append({"property": prop, "wins": wins, "empty": empty})
    return {"records": len(ctx.records), "golden": len(clusters), "properties": out}
