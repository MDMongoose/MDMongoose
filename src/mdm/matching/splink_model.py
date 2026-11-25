"""Exact keys + Splink: train a Fellegi-Sunter model with Splink (DuckDB); score with it in plain DuckDB SQL.

Training (mdm_match_train, occasional) estimates from the data:
    λ  the prior: how likely two random records match (from the exact keys, when there are any)
    u  how often each comparison level happens by chance (random sample of pairs)
    m  how often it happens for true matches (expectation maximisation, once per blocking rule)
and saves Splink's model JSON on the Match Rule as a draft (_model_draft); Activate makes it _model.

Every run reuses the saved model: the match weight of a pair is
    log2(λ / (1 - λ)) + Σ log2(m / u) of the level each comparison falls into
and its probability 2^w / (1 + 2^w). This is what Splink's predict() computes (without term-frequency
adjustments); doing it in our own SQL keeps incremental runs and explanations under our control.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone

from .compare import levels
from .spec import MatchSpec


def _q(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def settings(spec: MatchSpec, prior: float = 1e-4) -> dict:
    comps = []
    for f in spec.fields:
        if f.role != "splink":
            continue
        l, r = _q(f.key + "_l"), _q(f.key + "_r")
        lv = [{"sql_condition": f"{l} IS NULL OR {r} IS NULL", "label_for_charts": "missing", "is_null_level": True}]
        lv += [{"sql_condition": cond, "label_for_charts": label} for label, cond in levels(f.comparison, l, r)]
        lv.append({"sql_condition": "ELSE", "label_for_charts": "else"})
        comps.append({"output_column_name": f.key, "comparison_description": f"{f.name} ({f.comparison})",
                      "comparison_levels": lv})
    from .candidates import blocking_condition
    return {"link_type": "dedupe_only", "unique_id_column_name": "uid", "probability_two_random_records_match": prior,
            "comparisons": comps, "retain_matching_columns": False, "retain_intermediate_calculation_columns": False,
            "blocking_rules_to_generate_predictions": [
                {"blocking_rule": blocking_condition(spec, parts), "sql_dialect": "duckdb"}
                for parts in spec.blocking_parts()]}


def train(spec: MatchSpec, con, user: str, decisions: list[tuple[str, str, bool]] | None = None,
          max_pairs: float = 1e6) -> dict:
    """Train on the records in `recs` (all of them). Returns the model (Splink JSON + our metadata)."""
    from splink import DuckDBAPI, Linker, block_on  # noqa: F401 - imported only in the training job

    n = con.execute("SELECT count(*) FROM recs").fetchall()[0][0]
    if n < 10:
        raise ValueError(f"only {n} records — training needs data")
    keys = [f.key for f in spec.fields if f.role in ("splink", "blocking", "exact")]
    con.execute(f"CREATE OR REPLACE TABLE train_input AS SELECT uid, {', '.join(_q(k) for k in keys)} FROM recs")
    # Splink compares columns named <key>; our level SQL uses "<key>_l" / "<key>_r" like Splink's own
    linker = Linker("train_input", settings(spec), db_api=DuckDBAPI(connection=con))
    notes = []
    exact = [f for f in spec.exact_fields()]
    if exact:
        try:
            linker.training.estimate_probability_two_random_records_match(
                [f"l.{_q(f.key)} = r.{_q(f.key)}" for f in exact], recall=0.8)
        except Exception as e:  # noqa: BLE001
            notes.append(f"prior from exact keys failed ({e}); using 1 in 10 000")
    linker.training.estimate_u_using_random_sampling(max_pairs=max_pairs)
    from .candidates import blocking_condition
    for text, parts in zip(spec.blocking, spec.blocking_parts()):
        try:
            linker.training.estimate_parameters_using_expectation_maximisation(blocking_condition(spec, parts))
        except Exception as e:  # noqa: BLE001 - e.g. a blocking rule that yields no pairs
            notes.append(f"EM on blocking rule {text!r} skipped: {e}")
    model = linker.misc.save_model_to_json()
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    model["_version"] = hashlib.sha1(json.dumps(model, sort_keys=True, default=str).encode()).hexdigest()[:10]
    model["_trained_at"], model["_trained_by"], model["_records"] = stamp, user, int(n)
    model["_fields"] = {f.key: f.name for f in spec.fields if f.role == "splink"}
    model["_notes"] = notes
    if decisions:
        model["_accuracy"] = accuracy(spec, con, model, decisions)
    return model


def _log2(x: float) -> float:
    return math.log2(x)


def level_weights(model: dict) -> list[dict]:
    """[{comparison, levels: [{label, sql, weight|None}]}] — None: not trained / null level (weight 0)."""
    out = []
    for c in model.get("comparisons", []):
        lv = []
        for level in c.get("comparison_levels", []):
            m, u = level.get("m_probability"), level.get("u_probability")
            w = None if level.get("is_null_level") or not m or not u else _log2(m / u)
            lv.append({"label": level.get("label_for_charts"), "sql": level["sql_condition"], "weight": w,
                       "null": bool(level.get("is_null_level"))})
        out.append({"column": c.get("output_column_name"), "description": c.get("comparison_description"), "levels": lv})
    return out


def prior_weight(model: dict) -> float:
    p = float(model.get("probability_two_random_records_match") or 1e-4)
    return _log2(p / (1 - p))


def _case(levels_: list[dict], value: str) -> str:
    """CASE over the levels; value(level, index) -> SQL."""
    parts = []
    else_v = "0"
    for i, lv in enumerate(levels_):
        v = value(lv, i)
        if lv["sql"].strip().upper() == "ELSE":
            else_v = v
        else:
            parts.append(f"WHEN {lv['sql']} THEN {v}")
    return f"(CASE {' '.join(parts)} ELSE {else_v} END)"


def score_sql(spec: MatchSpec, model: dict) -> str:
    """SELECT over cmp -> a, b, score (probability), band, weight."""
    terms = [_case(c["levels"], lambda lv, i: repr(lv["weight"] or 0.0)) for c in level_weights(model)]
    w = f"{prior_weight(model)!r}" + "".join(f" + {t}" for t in terms)
    return f"""
        WITH s AS (SELECT a, b, {w} AS weight FROM cmp)
        SELECT a, b, round(1.0 / (1.0 + pow(2.0, -weight)), 4) AS score,
               CASE WHEN 1.0 / (1.0 + pow(2.0, -weight)) >= {spec.link_threshold} THEN 'link'
                    WHEN 1.0 / (1.0 + pow(2.0, -weight)) >= {spec.review_threshold} THEN 'review' END AS band,
               'Splink' AS rule, weight
        FROM s"""


def explain(spec: MatchSpec, con, model: dict, a: str, b: str) -> dict:
    cols = ", ".join(f"l.{_q(f.key)} AS {_q(f.key + '_l')}, r.{_q(f.key)} AS {_q(f.key + '_r')}" for f in spec.fields)
    comps = level_weights(model)
    sel = ", ".join(_case(c["levels"], lambda lv, i: str(i)) + f" AS g{j}" for j, c in enumerate(comps)) or "NULL"
    row = con.execute(f"WITH cmp AS (SELECT {cols} FROM recs l, recs r WHERE l.uid = ? AND r.uid = ?) "
                      f"SELECT {sel} FROM cmp", [a, b]).fetchall()
    total = prior_weight(model)
    out = []
    names = model.get("_fields") or {}
    for j, c in enumerate(comps):
        idx = row[0][j] if row else None
        lv = c["levels"][idx] if idx is not None else None
        w = (lv or {}).get("weight") or 0.0
        total += w
        out.append({"field": names.get(c["column"], c["column"]), "level": (lv or {}).get("label"), "weight": round(w, 2)})
    p = 1.0 / (1.0 + 2 ** -total)
    band = "link" if p >= spec.link_threshold else "review" if p >= spec.review_threshold else None
    return {"method": "splink", "prior": round(prior_weight(model), 2), "comparisons": out, "weight": round(total, 2),
            "score": round(p, 4), "band": band}


def accuracy(spec: MatchSpec, con, model: dict, decisions: list[tuple[str, str, bool]]) -> dict:
    """Precision / recall at the link threshold against steward decisions (a, b, same?)."""
    con.execute("CREATE OR REPLACE TABLE pairs (a VARCHAR, b VARCHAR)")
    known = {r[0] for r in con.execute("SELECT uid FROM recs").fetchall()}
    lab = [(min(a, b), max(a, b), s) for a, b, s in decisions if a in known and b in known]
    if not lab:
        return {"labels": 0}
    con.executemany("INSERT INTO pairs VALUES (?, ?)", [(a, b) for a, b, _ in lab])
    from .candidates import build_cmp
    build_cmp(con, spec)
    pred = {(a, b): s for a, b, s, *_ in con.execute(score_sql(spec, model)).fetchall()}
    tp = sum(1 for a, b, same in lab if same and pred.get((a, b), 0) >= spec.link_threshold)
    fp = sum(1 for a, b, same in lab if not same and pred.get((a, b), 0) >= spec.link_threshold)
    fn = sum(1 for a, b, same in lab if same and pred.get((a, b), 0) < spec.link_threshold)
    return {"labels": len(lab), "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None, "threshold": spec.link_threshold}
