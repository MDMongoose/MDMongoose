"""Record filters: which source records take part in matching and survivorship.

A Match Rule's Source Filters ("<tableUID>=<condition>", one per source) are SQL-like conditions over
the record's columns, joined with AND / OR (AND binds tighter):
    COL IS [NOT] NULL · COL = | <> | != 'x' · COL [NOT] LIKE | RLIKE '...' · COL · NOT COL
A bare column is true when it holds a value other than empty, false, 0, no or n.
Records that fail the filter of their source get no Golden UID and no golden record.
"""

from __future__ import annotations

import re
from typing import Callable

from ..hierarchy import RuleError

_ATOM = re.compile(r"""^\s*(?P<not>NOT\s+)?(?P<col>[A-Za-z_][\w]*|`[^`]+`|"[^"]+")\s*(?P<rest>.*?)\s*$""", re.I | re.S)
_FALSE = {"", "false", "0", "no", "n", "none", "null"}


def parse_filters(items) -> dict[str, str]:
    """["<UID>=<condition>", ...] -> {UID: condition}; the first '=' separates the table."""
    out = {}
    for it in items or []:
        if isinstance(it, str) and "=" in it:
            t, cond = it.split("=", 1)
            if t.strip() and cond.strip():
                out[t.strip().upper()] = cond.strip()
    return out


def filters_list(filters: dict[str, str]) -> list[str]:
    return [f"{t}={c.strip()}" for t, c in (filters or {}).items() if (c or "").strip()]


def compile_filter(expr: str | None) -> Callable[[dict], bool]:
    """The condition as a function of the record's properties."""
    from ..golden import _COND, _one_value
    if not expr or not expr.strip():
        return lambda props: True
    ors = []
    for part in re.split(r"\s+OR\s+", expr.strip(), flags=re.I):
        ands = []
        for atom in re.split(r"\s+AND\s+", part, flags=re.I):
            m = _ATOM.match(atom)
            if not m:
                raise RuleError(f"unsupported filter: {atom.strip()!r}")
            col, rest, neg = m.group("col").strip('`"'), m.group("rest"), bool(m.group("not"))
            if not rest:
                ands.append(_truthy(col, neg))
                continue
            if neg:
                raise RuleError(f"unsupported filter: {atom.strip()!r} — put NOT after the column")
            cm = _COND.match("value " + rest) or (_COND.match("value" + rest) if rest[:1] in "=<!" else None)
            f = _one_value(cm) if cm else None
            if f is None:
                raise RuleError(f"unsupported filter: {atom.strip()!r}")
            ands.append(_on(col, f))
        ors.append(ands)
    return lambda props: any(all(f(props) for f in ands) for ands in ors)


def _value(props: dict, col: str):
    if col in props:
        return props[col]
    low = col.lower()
    return next((v for k, v in props.items() if k.lower() == low), None)


def _on(col: str, f) -> Callable[[dict], bool]:
    def test(props):
        v = _value(props, col)
        return f(None if v is None or str(v).strip() == "" else str(v).strip())
    return test


def _truthy(col: str, neg: bool) -> Callable[[dict], bool]:
    def test(props):
        v = _value(props, col)
        t = v is not None and str(v).strip().lower() not in _FALSE
        return not t if neg else t
    return test


def check(filters: dict[str, str]) -> list[str]:
    errs = []
    for t, cond in (filters or {}).items():
        try:
            compile_filter(cond)
        except RuleError as e:
            errs.append(f"record filter of {t}: {e}")
    return errs


def passes(filters: dict[str, str]) -> Callable[[dict], bool]:
    """record -> bool, using the filter of the record's source table."""
    fns = {t: compile_filter(c) for t, c in (filters or {}).items()}
    return lambda rec: fns[rec["parent_uid"]](rec.get("properties") or {}) if rec.get("parent_uid") in fns else True
