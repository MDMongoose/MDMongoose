"""Comparison types of a Match Field.

similarity(l, r)  a number 0..1 used by fuzzy rules (NULL when either side is empty)
levels(l, r)      the comparison levels a Splink model learns a weight for (best first; then "else")

    Exact                       1 when equal
    Jaro-Winkler                Jaro-Winkler similarity          levels: exact · JW ≥ 0.92 · JW ≥ 0.80
    Jaro-Winkler order ignored  Jaro-Winkler, also with the words sorted (Doe John = John Doe)
                                                                 levels: exact · same words · JW ≥ 0.92 · JW ≥ 0.80
    Levenshtein                 1 - edits / longer length        levels: exact · 1 edit · 2 edits
    Damerau-Levenshtein         like Levenshtein, a swap of two neighbouring letters is one edit
                                                                 levels: exact · 1 edit · 2 edits
    Jaccard                     shared words / all words         levels: exact · ≥ 0.8 · ≥ 0.5
    Soundex                     1 when the Soundex codes are equal (sounds alike in English: Robert = Rupert)
                                                                 levels: exact · same Soundex
Email addresses, phone numbers, postcodes, dates and numbers are compared after cleaning them into one form
(Standardize, Regex Replace, Digits only ...), with Exact or one of the comparisons above.
"""

from __future__ import annotations


def _jw(l, r):
    return f"jaro_winkler_similarity({l}, {r})"


def _sorted(x):
    return f"array_to_string(list_sort(string_split({x}, ' ')), ' ')"


def _tok(x):
    return f"list_distinct(string_split({x}, ' '))"


def _jacc(l, r):
    return (f"(len(list_intersect({_tok(l)}, {_tok(r)}))::DOUBLE / "
            f"nullif(len(list_distinct(list_concat(string_split({l}, ' '), string_split({r}, ' ')))), 0))")


def _lev(l, r):
    return f"levenshtein({l}, {r})"


def _dl(l, r):
    return f"damerau_levenshtein({l}, {r})"


def _soundex(x):
    """Soundex in DuckDB SQL: first letter + 3 digits (B F P V → 1, C G J K Q S X Z → 2, D T → 3, L → 4,
    M N → 5, R → 6); H and W are skipped, vowels separate, equal neighbouring digits count once."""
    s = f"regexp_replace(upper(strip_accents({x})), '[^A-Z]', '', 'g')"
    d = f"translate(regexp_replace({s}, '[HW]', '', 'g'), 'ABCDEFGIJKLMNOPQRSTUVXYZ', '012301202245501262301202')"
    for n in "0123456":
        d = f"regexp_replace({d}, '{n}+', '{n}', 'g')"
    rest = f"CASE WHEN left({s}, 1) IN ('H', 'W') THEN {d} ELSE substr({d}, 2) END"
    return f"nullif(left({s}, 1) || rpad(left(replace({rest}, '0', ''), 3), 3, '0'), '000')"


def similarity(comparison: str, l: str, r: str) -> str:
    c = comparison
    if c == "Exact":
        s = f"CASE WHEN {l} = {r} THEN 1.0 ELSE 0.0 END"
    elif c == "Jaro-Winkler":
        s = _jw(l, r)
    elif c == "Jaro-Winkler order ignored":
        s = f"greatest({_jw(l, r)}, {_jw(_sorted(l), _sorted(r))})"
    elif c == "Levenshtein":
        s = f"1.0 - {_lev(l, r)}::DOUBLE / greatest(length({l}), length({r}), 1)"
    elif c == "Damerau-Levenshtein":
        s = f"1.0 - {_dl(l, r)}::DOUBLE / greatest(length({l}), length({r}), 1)"
    elif c == "Soundex":
        s = f"CASE WHEN {_soundex(l)} = {_soundex(r)} THEN 1.0 ELSE 0.0 END"
    elif c == "Jaccard":
        s = f"coalesce({_jacc(l, r)}, 0.0)"
    else:
        raise ValueError(f"unknown comparison {comparison}")
    return f"(CASE WHEN {l} IS NULL OR {r} IS NULL THEN NULL ELSE ({s})::DOUBLE END)"


def levels(comparison: str, l: str, r: str) -> list[tuple[str, str]]:
    """[(label, SQL condition)] best first; the caller adds the null level first and 'else' last."""
    c = comparison
    exact = ("exact", f"{l} = {r}")
    if c == "Exact":
        return [exact]
    if c == "Jaro-Winkler":
        return [exact, ("Jaro-Winkler ≥ 0.92", f"{_jw(l, r)} >= 0.92"), ("Jaro-Winkler ≥ 0.80", f"{_jw(l, r)} >= 0.8")]
    if c == "Jaro-Winkler order ignored":
        return [exact, ("same words, other order", f"{_sorted(l)} = {_sorted(r)}"),
                ("Jaro-Winkler ≥ 0.92", f"{_jw(l, r)} >= 0.92"), ("Jaro-Winkler ≥ 0.80", f"{_jw(l, r)} >= 0.8")]
    if c == "Levenshtein":
        return [exact, ("1 edit", f"{_lev(l, r)} <= 1"), ("2 edits", f"{_lev(l, r)} <= 2")]
    if c == "Damerau-Levenshtein":
        return [exact, ("1 edit (a swap counts as one)", f"{_dl(l, r)} <= 1"), ("2 edits", f"{_dl(l, r)} <= 2")]
    if c == "Soundex":
        return [exact, ("same Soundex", f"{_soundex(l)} = {_soundex(r)}")]
    if c == "Jaccard":
        return [exact, ("word overlap ≥ 0.8", f"{_jacc(l, r)} >= 0.8"), ("word overlap ≥ 0.5", f"{_jacc(l, r)} >= 0.5")]
    raise ValueError(f"unknown comparison {comparison}")


def describe(comparison: str) -> str:
    lv = [x[0] for x in levels(comparison, "l", "r")]
    return " · ".join(lv + ["else"])
