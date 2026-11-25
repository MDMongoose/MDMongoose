"""Exact keys + fuzzy rules: a pair links when ALL conditions of ANY rule pass their link value.

Each condition is "Field >= link (review r)" on the field's similarity (0..1, see compare).
    rule passes for link    every condition ≥ its link value
    rule passes for review  every condition ≥ its review value (default: the link value), not all ≥ link
    rule score              the weakest similarity among its conditions
Pair: the best linking rule's score (band link); else the best reviewing rule's score (band review);
else no match. Runs in DuckDB SQL — no model, no training.
"""

from __future__ import annotations

from .compare import similarity
from .spec import MatchSpec


def _q(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def _sim(spec: MatchSpec, field_name: str) -> str:
    f = spec.field_by_name(field_name)
    return similarity(f.comparison, _q(f.key + "_l"), _q(f.key + "_r"))


def score_sql(spec: MatchSpec) -> str:
    """SELECT over cmp -> a, b, score, band ('link' | 'review' | NULL), rule (name of the deciding rule)."""
    rule_cols = []
    for i, r in enumerate(spec.fuzzy_rules):
        sims = [f"coalesce({_sim(spec, c.field)}, -1.0)" for c in r.conditions]
        link = " AND ".join(f"{s} >= {c.link}" for s, c in zip(sims, r.conditions)) or "FALSE"
        review = " AND ".join(f"{s} >= {c.review_at}" for s, c in zip(sims, r.conditions)) or "FALSE"
        weakest = f"least({', '.join(sims)})" if len(sims) > 1 else (sims[0] if sims else "0.0")
        rule_cols.append(f"CASE WHEN {link} THEN 2.0 + {weakest} WHEN {review} THEN 1.0 + {weakest} ELSE 0.0 END AS r{i}")
    if not rule_cols:
        return "SELECT a, b, 0.0 AS score, NULL AS band, NULL AS rule FROM cmp WHERE FALSE"
    best = f"greatest({', '.join(f'r{i}' for i in range(len(rule_cols)))})" if len(rule_cols) > 1 else "r0"
    names = " ".join(f"WHEN r{i} = best THEN '{r.name.replace(chr(39), chr(39) * 2)}'" for i, r in enumerate(spec.fuzzy_rules))
    return f"""
        WITH s AS (SELECT a, b, {', '.join(rule_cols)} FROM cmp),
             t AS (SELECT *, {best} AS best FROM s)
        SELECT a, b,
               round(CASE WHEN best >= 2 THEN best - 2 WHEN best >= 1 THEN best - 1 ELSE 0 END, 4) AS score,
               CASE WHEN best >= 2 THEN 'link' WHEN best >= 1 THEN 'review' END AS band,
               CASE {names} END AS rule
        FROM t WHERE best >= 1"""


def explain(spec: MatchSpec, con, a: str, b: str) -> dict:
    """Every rule and condition for one pair (both records must be in recs)."""
    fields = {c.field for r in spec.fuzzy_rules for c in r.conditions}
    sel = ", ".join(f"{_sim(spec, n)} AS {_q('s_' + n)}" for n in sorted(fields)) or "NULL AS none"
    cols = ", ".join(f"l.{_q(f.key)} AS {_q(f.key + '_l')}, r.{_q(f.key)} AS {_q(f.key + '_r')}" for f in spec.fields)
    row = con.execute(f"""WITH cmp AS (SELECT {cols} FROM recs l, recs r WHERE l.uid = ? AND r.uid = ?)
                          SELECT {sel} FROM cmp""", [a, b]).fetchall()
    sims = dict(zip(sorted(fields), row[0])) if row and fields else {}
    rules, best = [], (0, None, None)
    for r in spec.fuzzy_rules:
        conds, ok_link, ok_rev = [], True, True
        for c in r.conditions:
            s = sims.get(c.field)
            passed = "link" if s is not None and s >= c.link else "review" if s is not None and s >= c.review_at else "fail"
            ok_link &= passed == "link"
            ok_rev &= passed in ("link", "review")
            conds.append({"field": c.field, "test": c.text(), "similarity": None if s is None else round(s, 3),
                          "result": passed})
        vals = [x["similarity"] for x in conds]
        weakest = min(vals) if vals and None not in vals else None
        status = "link" if ok_link and conds else "review" if ok_rev and conds else "no"
        rules.append({"name": r.name, "status": status, "score": weakest, "conditions": conds})
        rank = {"link": 2, "review": 1}.get(status, 0)
        if rank and (rank, weakest or 0) > (best[0], best[1] or 0):
            best = (rank, weakest, r.name)
    return {"method": "fuzzy", "rules": rules,
            "band": {2: "link", 1: "review"}.get(best[0]), "score": best[1], "rule": best[2]}
