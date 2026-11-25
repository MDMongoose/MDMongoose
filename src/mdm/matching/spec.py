"""Match Rule node -> MatchSpec.

Model › Rules › <Match Rule>            Sources, Source Filters, Method, Blocking Rules, Link / Review Threshold, _model
    <Match Field> (ordered)             Role, Comparison, Cleaning, Source Mapping "<tableUID>=<COLUMN>; ..."
    <Fuzzy Rule>  (ordered)             Conditions ["Legal Name >= 0.92 review 0.85", "Country >= 1"]
    Pairs (folder)                      Match Pair nodes: Record A, Record B, Score, Status, Decided By ...
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from ..hierarchy import RuleError
from ..model import K, new_node
from ..uid import to_uid

# ---------------------------------------------------------------------------- enums (seeded UIDs)
METHOD_EXACT, METHOD_FUZZY, METHOD_SPLINK = to_uid(1411), to_uid(1412), to_uid(1413)
METHODS = {METHOD_EXACT: "exact", METHOD_FUZZY: "fuzzy", METHOD_SPLINK: "splink"}
METHOD_LABELS = {"exact": "Exact keys only", "fuzzy": "Exact keys + fuzzy rules", "splink": "Exact keys + Splink"}
METHOD_BY_KEY = {v: k for k, v in METHODS.items()}

ROLE_EXACT, ROLE_FUZZY, ROLE_SPLINK, ROLE_BLOCKING = to_uid(1416), to_uid(1417), to_uid(1418), to_uid(1419)
ROLES = {ROLE_EXACT: "exact", ROLE_FUZZY: "fuzzy", ROLE_SPLINK: "splink", ROLE_BLOCKING: "blocking"}
ROLE_LABELS = {"exact": "Exact key", "fuzzy": "Fuzzy rule", "splink": "Splink", "blocking": "Blocking only"}
ROLE_BY_KEY = {v: k for k, v in ROLES.items()}

# enum values of Comparisons, by seed number (stable UIDs); COMPARISON_NAMES is the order offered in the app
# (1426-1430 were Email, Phone, Postcode, Date, Number: removed — standardize such columns and use these)
COMPARISON_NUMS = {"Exact": 1421, "Jaro-Winkler": 1422, "Jaro-Winkler order ignored": 1423, "Levenshtein": 1424,
                   "Jaccard": 1425, "Damerau-Levenshtein": 1431, "Soundex": 1432}
COMPARISON_NAMES = ["Exact", "Levenshtein", "Damerau-Levenshtein", "Jaro-Winkler", "Jaro-Winkler order ignored", "Jaccard",
                    "Soundex"]
REMOVED_COMPARISONS = {to_uid(1426): to_uid(1421), to_uid(1427): to_uid(1424), to_uid(1428): to_uid(1424),
                       to_uid(1429): to_uid(1424), to_uid(1430): to_uid(1421)}  # old -> what mdm_init sets instead
COMPARISONS = {to_uid(n): name for name, n in COMPARISON_NUMS.items()}
COMPARISON_BY_NAME = {v: k for k, v in COMPARISONS.items()}

STATUS_OPEN, STATUS_SAME, STATUS_NOT = to_uid(1436), to_uid(1437), to_uid(1438)
STATUS_LABELS = {STATUS_OPEN: "Open", STATUS_SAME: "Same entity", STATUS_NOT: "Not the same"}
STATUS_BY_KEY = {"open": STATUS_OPEN, "same": STATUS_SAME, "not": STATUS_NOT}
STATUS_KEYS = {v: k for k, v in STATUS_BY_KEY.items()}

DEFAULT_LINK, DEFAULT_REVIEW = 0.95, 0.70
PAIRS_FOLDER = "Pairs"
MATCH_PROPS = ("Golden UID", "Match Score", "Matched By", "Matched At", "_match_hash")


def parse_mapping(text: str | None) -> dict[str, str]:
    out = {}
    for part in (text or "").split(";"):
        if "=" in part:
            t, col = part.split("=", 1)
            if col.strip():
                out[t.strip().upper()] = col.strip()
    return out


def mapping_text(mapping: dict[str, str]) -> str:
    return "; ".join(f"{t}={c}" for t, c in mapping.items() if c)


# ---------------------------------------------------------------------------- parts of a rule
@dataclass
class MatchField:
    uid: str | None
    name: str
    role: str  # exact | fuzzy | splink | blocking
    comparison: str  # one of COMPARISON_NAMES
    cleaning: list[str]
    mapping: dict[str, str]  # table uid -> record property

    @property
    def key(self) -> str:
        """SQL-safe column name: f_<name>. A Splink model refers to it, so renaming a field means retraining."""
        return "f_" + (re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_") or "field")

    def to_dict(self) -> dict:
        return {"uid": self.uid, "name": self.name, "role": self.role, "comparison": self.comparison,
                "cleaning": list(self.cleaning), "mapping": dict(self.mapping)}


@dataclass
class FuzzyCondition:
    field: str
    link: float
    review: float | None = None

    @property
    def review_at(self) -> float:
        return self.link if self.review is None else min(self.review, self.link)

    def text(self) -> str:
        return f"{self.field} >= {self.link:g}" + (f" review {self.review:g}" if self.review is not None else "")


_COND = re.compile(r"^\s*(?P<f>.+?)\s*(?:>=|≥)\s*(?P<link>[0-9]*\.?[0-9]+)(?:\s+review\s+(?P<rev>[0-9]*\.?[0-9]+))?\s*$",
                   re.I)


def parse_condition(text: str) -> FuzzyCondition:
    m = _COND.match(text or "")
    if not m:
        raise RuleError(f"fuzzy condition {text!r}: write it as 'Field >= 0.9' or 'Field >= 0.9 review 0.8'")
    link = float(m.group("link"))
    rev = float(m.group("rev")) if m.group("rev") else None
    if not 0 <= link <= 1 or (rev is not None and not 0 <= rev <= 1):
        raise RuleError(f"fuzzy condition {text!r}: similarities are between 0 and 1")
    return FuzzyCondition(m.group("f").strip(), link, rev)


@dataclass
class FuzzyRule:
    uid: str | None
    name: str
    conditions: list[FuzzyCondition]

    def to_dict(self) -> dict:
        return {"uid": self.uid, "name": self.name,
                "conditions": [{"field": c.field, "link": c.link, "review": c.review} for c in self.conditions]}


_BLOCK_PART = re.compile(r"^\s*(?P<f>[^\[\]]+?)\s*(?:\[(?P<a>-?\d*):(?P<b>-?\d*)\])?\s*$")


@dataclass
class BlockPart:
    field: str
    first: int | None = None  # first N characters
    last: int | None = None  # last N characters

    def text(self) -> str:
        return self.field + (f"[:{self.first}]" if self.first else f"[-{self.last}:]" if self.last else "")


def parse_blocking(text: str) -> list[BlockPart]:
    """'Country & Legal Name[:3]' -> same Country and same first 3 characters of Legal Name.
    'Phone[-7:]' -> same last 7 characters."""
    parts = []
    for raw in re.split(r"\s*&\s*|\s+AND\s+", text or "", flags=re.I):
        if not raw.strip():
            continue
        m = _BLOCK_PART.match(raw)
        if not m:
            raise RuleError(f"blocking rule {text!r}: use 'Field', 'Field[:3]' or 'Field[-7:]', joined with &")
        a, b = m.group("a"), m.group("b")
        part = BlockPart(m.group("f").strip())
        if a is not None and a.startswith("-") and not b:
            part.last = int(a[1:])
        elif a in ("", None) and b:
            part.first = int(b)
        elif a is not None or b is not None:
            raise RuleError(f"blocking rule {text!r}: only [:N] (first N) and [-N:] (last N) are supported")
        parts.append(part)
    if not parts:
        raise RuleError("a blocking rule needs at least one field")
    return parts


def blocking_label(parts: list[BlockPart]) -> str:
    out = []
    for p in parts:
        out.append(f"same first {p.first} characters of {p.field}" if p.first else
                   f"same last {p.last} characters of {p.field}" if p.last else f"same {p.field}")
    s = " and ".join(out)
    return s[:1].upper() + s[1:]


@dataclass
class MatchSpec:
    rule: dict
    sources: list[dict]
    method: str
    fields: list[MatchField]
    fuzzy_rules: list[FuzzyRule]
    blocking: list[str]
    link_threshold: float = DEFAULT_LINK
    review_threshold: float = DEFAULT_REVIEW
    model: dict | None = None
    pairs_folder: dict | None = None
    countries: dict[str, str] = field(default_factory=dict)  # lower(code / name / uid) -> canonical name
    filters: dict[str, str] = field(default_factory=dict)  # table uid -> record filter (see .filters)
    tables: dict[str, dict] = field(default_factory=dict)  # Standardization Table uid -> lookup (Standardize step)
    table_errors: list[str] = field(default_factory=list)

    @property
    def uid(self) -> str:
        return self.rule["uid"]

    def field_by_name(self, name: str) -> MatchField | None:
        n = (name or "").strip().lower()
        return next((f for f in self.fields if f.name.lower() == n), None)

    def exact_fields(self) -> list[MatchField]:
        return [f for f in self.fields if f.role == "exact"]

    def scored_fields(self) -> list[MatchField]:
        role = {"fuzzy": "fuzzy", "splink": "splink"}.get(self.method)
        return [f for f in self.fields if role and f.role == role]

    def blocking_parts(self) -> list[list[BlockPart]]:
        return [parse_blocking(b) for b in self.blocking]

    def fingerprint(self) -> str:
        """Changes when anything that decides the result changes: then every record's _match_hash
        differs and the next run re-matches everything."""
        cfg = {"method": self.method, "fields": [f.to_dict() | {"uid": None} for f in self.fields],
               "fuzzy": [r.to_dict() | {"uid": None} for r in self.fuzzy_rules] if self.method == "fuzzy" else [],
               "blocking": self.blocking, "link": self.link_threshold, "review": self.review_threshold,
               "model": (self.model or {}).get("_version") if self.method == "splink" else None,
               "sources": [s["uid"] for s in self.sources]}
        if any(self.filters.values()):  # only when set, so rules without filters keep their fingerprint
            cfg["filters"] = {k: v for k, v in sorted(self.filters.items()) if v}
        return hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]

    def validate(self) -> list[str]:
        errs = []
        if not self.sources:
            errs.append("the rule has no Sources")
        if not self.fields:
            errs.append("the rule has no Match Fields")
        names = [f.name.lower() for f in self.fields]
        if len(set(names)) != len(names):
            errs.append("two Match Fields have the same name")
        keys = [f.key for f in self.fields]
        if len(set(keys)) != len(keys):
            errs.append("two Match Fields differ only in punctuation — rename one")
        src = {s["uid"] for s in self.sources}
        for f in self.fields:
            if f.comparison not in COMPARISON_NAMES:
                errs.append(f"{f.name}: unknown comparison {f.comparison!r}")
            if not any(t in src for t in f.mapping):
                errs.append(f"{f.name}: map at least one source column")
            from .standardise import check_steps
            errs += [f"{f.name}: {e}" for e in check_steps(f.cleaning)]
        for b in self.blocking:
            try:
                for p in parse_blocking(b):
                    if not self.field_by_name(p.field):
                        errs.append(f"blocking rule {b!r}: no Match Field {p.field!r}")
            except RuleError as e:
                errs.append(str(e))
        if self.method == "fuzzy":
            if not self.fuzzy_rules:
                errs.append("Exact keys + fuzzy rules needs at least one fuzzy rule")
            for r in self.fuzzy_rules:
                if not r.conditions:
                    errs.append(f"fuzzy rule {r.name!r} has no conditions")
                for c in r.conditions:
                    f = self.field_by_name(c.field)
                    if not f:
                        errs.append(f"fuzzy rule {r.name!r}: no Match Field {c.field!r}")
        if self.method == "splink" and not [f for f in self.fields if f.role == "splink"]:
            errs.append("Exact keys + Splink needs at least one field with the role Splink")
        from .filters import check
        errs += check(self.filters)
        errs += self.table_errors
        if not 0 < self.review_threshold <= self.link_threshold <= 1:
            errs.append("thresholds: 0 < review ≤ link ≤ 1")
        return errs


# ---------------------------------------------------------------------------- loading
def field_from_node(n: dict) -> MatchField:
    p = n["properties"] or {}
    return MatchField(uid=n["uid"], name=n["name"], role=ROLES.get(p.get("Role"), "splink"),
                      comparison=COMPARISONS.get(p.get("Comparison"), "Exact"),
                      cleaning=[str(x) for x in p.get("Cleaning") or []],
                      mapping=parse_mapping(p.get("Source Mapping")))


def fuzzy_from_node(n: dict) -> FuzzyRule:
    p = n["properties"] or {}
    return FuzzyRule(uid=n["uid"], name=n["name"], conditions=[parse_condition(c) for c in p.get("Conditions") or []])


def country_lookup(repo) -> dict[str, str]:
    """lower(name | Alpha2 | Alpha3 | UID) -> lower(name), from every Enum named Countries."""
    out: dict[str, str] = {}
    for e in repo.by_kind("Enum"):
        if e["name"].lower() != "countries":
            continue
        for c in repo.children(e["uid"]):
            name = c["name"].strip().lower()
            p = c["properties"] or {}
            for k in (c["name"], p.get("Alpha2"), p.get("Alpha3"), c["uid"]):
                if k:
                    out[str(k).strip().lower()] = name
    return out


STD_KEEP_SOURCE, STD_OVERRIDE = to_uid(1446), to_uid(1447)


def standardization_table(repo, uid: str) -> dict:
    """A Standardization Table node -> {uid, name, columns, key, default, map: lower(value) -> key}.
    Its rows are its children; a value of any Standardization Column (case-insensitive) maps to the row's
    Standardization Key. default = the Default Value with Default Method "Override default", else None."""
    node = repo.get(uid)
    if not node or K.NT_STANDARDIZATION_TABLE not in node["types"]:
        raise RuleError(f"{uid} is not a Standardization Table")
    p = node["properties"] or {}
    cols, key = list(p.get("Standardization Columns") or []), p.get("Standardization Key")
    if not cols or not key:
        raise RuleError(f"Standardization Table '{node['name']}': set Standardization Columns and Standardization Key")
    get = lambda r, c: r["name"] if c == "Name" else (r.get("properties") or {}).get(c)  # noqa: E731
    out: dict[str, str] = {}
    for r in repo.children(uid):
        k = get(r, key)
        if k in (None, ""):
            continue
        for c in cols:
            v = get(r, c)
            if v not in (None, ""):
                out.setdefault(str(v).strip().lower(), str(k))
    override = p.get("Default Method") == STD_OVERRIDE
    return {"uid": uid, "name": node["name"], "columns": cols, "key": key, "map": out,
            "default": (p.get("Default Value") or "") if override else None}


def word_list(repo, uid: str) -> dict:
    """An Enum node -> {uid, name, words}: its values, for a Drop words step."""
    node = repo.get(uid)
    if not node or (K.NT_ENUM not in node["types"] and (node.get("properties") or {}).get("kind") != "Enum"):
        raise RuleError(f"{uid} is not an Enum")
    return {"uid": uid, "name": node["name"], "kind": "words", "words": [c["name"] for c in repo.children(uid)]}


def standardization_tables(repo, steps: list[str]) -> tuple[dict[str, dict], list[str]]:
    """The nodes the steps refer to: Standardization Tables (Standardize) and Enums (Drop words)."""
    from .standardise import std_table
    from .standardise import word_list as words_of
    out, errs = {}, []
    for s in steps:
        u, load = std_table(s), standardization_table
        if not u:
            u, load = words_of(s), word_list
        if u and u not in out:
            try:
                out[u] = load(repo, u)
            except RuleError as e:
                errs.append(str(e))
    return out, errs


def load_spec(repo, rule_uid: str, overrides: dict | None = None) -> MatchSpec:
    """The Match Rule as stored, or with `overrides` from the editor (unsaved changes)."""
    rule = repo.get(rule_uid)
    if not rule or K.NT_MATCH_RULE not in rule["types"]:
        raise RuleError(f"{rule_uid} is not a Match Rule")
    p = dict(rule["properties"] or {})
    kids = repo.children(rule["uid"])
    fields = [field_from_node(n) for n in kids if K.NT_MATCH_FIELD in n["types"]]
    fuzzy = []
    for n in kids:
        if K.NT_FUZZY_RULE in n["types"]:
            try:
                fuzzy.append(fuzzy_from_node(n))
            except RuleError:
                fuzzy.append(FuzzyRule(n["uid"], n["name"], []))
    pairs = next((n for n in kids if n["name"] == PAIRS_FOLDER and K.NT_FOLDER in n["types"]), None)
    ov = overrides or {}
    if ov.get("sources") is not None:
        p["Sources"] = ov["sources"]
    sources = [s for s in (repo.get(u) for u in p.get("Sources") or []) if s]
    method = ov.get("method") or METHODS.get(p.get("Method"), "fuzzy")
    if ov.get("fields") is not None:
        fields = [MatchField(f.get("uid"), f["name"].strip(), f.get("role") or "splink", f.get("comparison") or "Exact",
                             list(f.get("cleaning") or []), {k.upper(): v for k, v in (f.get("mapping") or {}).items() if v})
                  for f in ov["fields"] if (f.get("name") or "").strip()]
    if ov.get("fuzzy_rules") is not None:
        fuzzy = [FuzzyRule(r.get("uid"), r.get("name") or f"Rule {i + 1}",
                           [FuzzyCondition(c["field"], float(c["link"]),
                                           None if c.get("review") in (None, "") else float(c["review"]))
                            for c in r.get("conditions") or [] if c.get("field")])
                 for i, r in enumerate(ov["fuzzy_rules"])]
    blocking = list(ov["blocking"] if ov.get("blocking") is not None else p.get("Blocking Rules") or [])
    link = float(ov.get("link_threshold") or p.get("Link Threshold") or DEFAULT_LINK)
    review = float(ov.get("review_threshold") or p.get("Review Threshold") or DEFAULT_REVIEW)
    model = p.get("_model")
    if isinstance(model, str):
        try:
            model = json.loads(model)
        except ValueError:
            model = None
    from .filters import parse_filters
    filters = parse_filters(p.get("Source Filters"))
    if ov.get("filters") is not None:
        filters = {k.upper(): v for k, v in ov["filters"].items() if (v or "").strip()} if isinstance(ov["filters"], dict) \
            else parse_filters(ov["filters"])
    filters = {k: v for k, v in filters.items() if k in {s["uid"] for s in sources}}
    spec = MatchSpec(rule=rule, sources=sources, method=method, fields=fields, fuzzy_rules=fuzzy, blocking=blocking,
                     link_threshold=link, review_threshold=review, model=model, pairs_folder=pairs, filters=filters)
    if any("country" in s.lower() for f in fields for s in f.cleaning):
        spec.countries = country_lookup(repo)
    spec.tables, spec.table_errors = standardization_tables(repo, [s for f in fields for s in f.cleaning])
    return spec


def ensure_pairs_folder(repo, spec: MatchSpec, user: str) -> dict:
    if spec.pairs_folder:
        return spec.pairs_folder
    num = repo.allocate(1)[0]
    folder = new_node(num, PAIRS_FOLDER, [K.NT_FOLDER], spec.rule, icon="pair", max_child_depth=0,
                      allowed_child_types=[K.NT_MATCH_PAIR])
    repo.upsert([folder], user)
    spec.pairs_folder = folder
    return folder


def rule_for_master(repo, master: dict) -> str | None:
    return (master.get("properties") or {}).get("Match Rule")


def masters_for_rule(repo, rule_uid: str) -> list[dict]:
    return [m for m in repo.by_types([K.NT_MASTER_DATA]) if (m["properties"] or {}).get("Match Rule") == rule_uid]
