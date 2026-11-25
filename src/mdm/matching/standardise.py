"""Cleaning steps -> DuckDB SQL. One library for exact keys, fuzzy rules, Splink and the app's previews.

A Match Field's Cleaning is an ordered list of steps; each step rewrites the value `x`:

    Trim                 trim, collapse inner whitespace
    Lower-case / Upper-case
    Remove accents       Müller -> Muller, Łódź -> Lodz, Straße -> Strasse
    Remove punctuation   punctuation becomes a space
    Drop words <UID>     the values of the Enum <UID> removed as whole words (any case, a trailing dot too;
                         dotted words like s.r.o also with spaces) — the LegalForms enum drops GmbH, Kft., S.A. ...
    Digits only          keeps 0-9
    Standardize <UID>    a value found in any Standardization Column of the Standardization Table <UID>
                         (case-insensitive) becomes the row's Standardization Key; else the Default Value
                         (Default Method "Override default") or the value itself ("Keep source")
    Empty markers        '', n/a, na, -, none, null, unknown, 0, ? -> empty
    Regex Replace 'regex' 'with'   regular expression replace (RE2 syntax)
Phone, e-mail and postcode cleaning are Regex Replace steps (see PHONE_CLEANING ...); countries use a Standardize
step. Rules not yet upgraded by mdm_init may still hold the old steps Drop legal forms, Replace, Phone, Email,
Postcode and Country; they still run but are no longer offered.

Empty results are NULL, and NULL never matches anything.
"""

from __future__ import annotations

import re

LEGAL_FORMS_RE2 = (r"\b(gmbh|ag|zrt|kft|nyrt|bt|ltd|limited|inc|incorporated|corp|corporation|co|llc|sa|sarl|srl|sro|"
                   r"bv|nv|plc|se|kg|ohg|ug|spa|sas|kk|pty)\b\.?")
LEGAL_DOTTED_RE2 = r"\b(s\.\s?r\.\s?o|a\.\s?s|s\.\s?r\.\s?l|s\.\s?a|b\.\s?v|n\.\s?v|s\.\s?p\.\s?a)(\.|\b)"
EMPTY_MARKERS = ("", "n/a", "na", "-", "--", "none", "null", "unknown", "0", "?", "nan")

STEP_NAMES = ["Trim", "Lower-case", "Upper-case", "Remove accents", "Remove punctuation", "Drop words",
              "Digits only", "Standardize", "Empty markers", "Regex Replace"]
LEGACY_STEPS = ["Drop legal forms", "Replace", "Phone", "Email", "Postcode", "Country"]  # still run, no longer offered

# what the removed steps became (mdm_init rewrites old rules with these)
EMAIL_CLEANING = ["Trim", "Lower-case", "Regex Replace '^[^@]*$' ''"]
POSTCODE_CLEANING = ["Upper-case", "Regex Replace '[\\s-]' ''"]


def phone_cleaning(country_code: str = "") -> list[str]:
    """Digits only; a 00 prefix dropped; a leading 0 becomes the country code (when given)."""
    out = ["Digits only", "Regex Replace '^00' ''"]
    return out + [f"Regex Replace '^0' '{country_code}'"] if country_code else out
STEP_HELP = {
    "Trim": "trim, collapse inner spaces",
    "Lower-case": "lower-case",
    "Upper-case": "upper-case",
    "Remove accents": "Müller → Muller",
    "Remove punctuation": "punctuation → space",
    "Drop words": "Drop words <enum>: the enum's values removed as whole words (e.g. LegalForms: GmbH, Kft., S.A.)",
    "Digits only": "keeps 0-9",
    "Standardize": "Standardize <table>: value found in a Standardization Table → its key, else the default",
    "Empty markers": "n/a, -, none, unknown, 0 … → empty",
    "Regex Replace": "Regex Replace 'regex' 'with' (RE2)",
}
_REPLACE = re.compile(r"^(?:Regex\s+)?Replace\s+'((?:[^']|'')*)'\s+'((?:[^']|'')*)'\s*$", re.I)
_STD = re.compile(r"^Standardize\s+(MDM-[0-9A-Z]+)\s*$", re.I)
_WORDS = re.compile(r"^Drop\s+words\s+(MDM-[0-9A-Z]+)\s*$", re.I)
_PHONE = re.compile(r"^Phone(?:\s+\+?(\d{1,4}))?\s*$", re.I)


def _lit(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def step_name(step: str) -> str:
    s = step.strip()
    for n in sorted(STEP_NAMES + LEGACY_STEPS, key=len, reverse=True):
        if s.lower() == n.lower() or s.lower().startswith(n.lower() + " "):
            return n
    return s


def check_steps(steps: list[str]) -> list[str]:
    errs = []
    for s in steps:
        n = step_name(s)
        if n not in STEP_NAMES + LEGACY_STEPS:
            errs.append(f"unknown cleaning step {s!r}")
        elif n in ("Regex Replace", "Replace"):
            m = _REPLACE.match(s)
            if not m:
                errs.append(f"{s!r}: write Regex Replace 'regex' 'replacement'")
            else:
                try:
                    re.compile(m.group(1).replace("''", "'"))
                except re.error as e:
                    errs.append(f"{s!r}: {e}")
        elif n == "Drop words" and not _WORDS.match(s):
            errs.append(f"{s!r}: write Drop words <UID of an Enum>")
        elif n == "Standardize" and not _STD.match(s):
            errs.append(f"{s!r}: write Standardize <UID of a Standardization Table>")
        elif n == "Phone" and not _PHONE.match(s):
            errs.append(f"{s!r}: write Phone or Phone +49")
    return errs


def _squash(e: str) -> str:
    return f"trim(regexp_replace({e}, '\\s+', ' ', 'g'))"


def std_table(step: str) -> str | None:
    """The Standardization Table UID of a Standardize step."""
    m = _STD.match(step.strip())
    return m.group(1).upper() if m else None


def word_list(step: str) -> str | None:
    """The Enum UID of a Drop words step."""
    m = _WORDS.match(step.strip())
    return m.group(1).upper() if m else None


def _rx(word: str) -> str:
    """A word as an RE2 pattern: special characters escaped, a dot may be followed by a space (s. r. o.)."""
    out = ""
    for ch in word.strip().rstrip("."):
        out += "\\.\\s?" if ch == "." else ("\\" + ch if ch in "^$*+?()[]{}|\\" else ch)
    return out


def drop_words_sql(x: str, words: list[str]) -> str:
    """Remove the words (any case, whole words, a trailing dot too), then tidy spaces and a trailing comma."""
    plain = [_rx(w) for w in words if w.strip() and "." not in w.strip().rstrip(".")]
    dotted = [_rx(w) for w in words if "." in w.strip().rstrip(".")]
    e = x
    if dotted:
        rx = "\\b(" + "|".join(sorted(dotted, key=len, reverse=True)) + ")(\\.|\\b)"
        e = f"regexp_replace({e}, {_lit(rx)}, ' ', 'gi')"
    if plain:
        rx = "\\b(" + "|".join(sorted(plain, key=len, reverse=True)) + ")\\b\\.?"
        e = f"regexp_replace({e}, {_lit(rx)}, ' ', 'gi')"
    return _squash(f"regexp_replace({e}, '[,&]+\\s*$', '', 'g')")


def step_sql(step: str, x: str, countries: dict[str, str] | None = None, tables: dict | None = None) -> str:
    n = step_name(step)
    if n == "Trim":
        return _squash(x)
    if n == "Lower-case":
        return f"lower({x})"
    if n == "Upper-case":
        return f"upper({x})"
    if n == "Remove accents":
        return f"replace(replace(translate(strip_accents({x}), 'łŁøØđĐıæÆ', 'lLoOdDiaA'), 'ß', 'ss'), 'œ', 'oe')"
    if n == "Remove punctuation":
        return _squash(f"regexp_replace({x}, '[^\\w\\s]', ' ', 'g')")
    if n == "Drop legal forms":
        e = f"regexp_replace({x}, {_lit(LEGAL_DOTTED_RE2)}, ' ', 'gi')"
        e = f"regexp_replace({e}, {_lit(LEGAL_FORMS_RE2)}, ' ', 'gi')"
        return _squash(f"regexp_replace({e}, '[,&]+\\s*$', '', 'g')")
    if n == "Digits only":
        return f"regexp_replace({x}, '[^0-9]', '', 'g')"
    if n == "Phone":
        m = _PHONE.match(step.strip())
        cc = m.group(1) if m and m.group(1) else ""
        d = f"regexp_replace(regexp_replace({x}, '[^0-9+]', '', 'g'), '^(\\+|00)', '', 'g')"
        if cc:
            return f"CASE WHEN {x} LIKE '+%' OR {x} LIKE '00%' THEN {d} WHEN {d} LIKE '0%' THEN {_lit(cc)} || substr({d}, 2) ELSE {d} END"
        return d
    if n == "Email":
        return f"CASE WHEN regexp_matches(lower(trim({x})), '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$') THEN lower(trim({x})) END"
    if n == "Postcode":
        return f"upper(regexp_replace({x}, '[\\s\\-]', '', 'g'))"
    if n == "Country":
        if not countries:
            return f"lower(trim({x}))"
        items = ", ".join(f"{_lit(k)}: {_lit(v)}" for k, v in sorted(countries.items()))
        return f"coalesce(map_extract(MAP {{{items}}}, lower(trim({x})))[1], lower(trim({x})))"
    if n == "Standardize":
        t = (tables or {}).get(std_table(step) or "")
        if t is None:
            raise ValueError(f"{step!r}: no such Standardization Table")
        v = f"lower(trim({x}))"
        hit = (f"map_extract(MAP {{{', '.join(f'{_lit(k)}: {_lit(w)}' for k, w in sorted(t['map'].items()))}}}, {v})[1]"
               if t["map"] else "NULL")
        miss = _lit(t["default"]) if t.get("default") is not None else x
        return f"CASE WHEN {x} IS NULL OR trim({x}) = '' THEN NULL ELSE coalesce({hit}, {miss}) END"
    if n == "Empty markers":
        return f"CASE WHEN lower(trim({x})) IN ({', '.join(_lit(v) for v in EMPTY_MARKERS)}) THEN NULL ELSE {x} END"
    if n == "Drop words":
        t = (tables or {}).get(word_list(step) or "")
        if t is None:
            raise ValueError(f"{step!r}: no such Enum")
        return drop_words_sql(x, t["words"]) if t["words"] else x
    if n in ("Regex Replace", "Replace"):
        m = _REPLACE.match(step.strip())
        if not m:
            raise ValueError(f"bad Replace step {step!r}")
        return f"regexp_replace({x}, {_lit(m.group(1).replace(chr(39) * 2, chr(39)))}, {_lit(m.group(2).replace(chr(39) * 2, chr(39)))}, 'g')"
    raise ValueError(f"unknown cleaning step {step!r}")


def clean_sql(steps: list[str], col: str, countries: dict[str, str] | None = None, tables: dict | None = None) -> str:
    """The cleaned value of column `col` as one SQL expression (NULL when empty)."""
    e = f"CAST({col} AS VARCHAR)"
    for s in steps:
        e = step_sql(s, e, countries, tables)
    return f"nullif(trim({e}), '')"


def clean_values(steps: list[str], values: list, countries: dict[str, str] | None = None,
                 tables: dict | None = None) -> list[str | None]:
    """Run the cleaning on a few values (previews in the app)."""
    import duckdb
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE v (i INTEGER, x VARCHAR)")
        if values:
            con.executemany("INSERT INTO v VALUES (?, ?)", [(i, None if v is None else str(v)) for i, v in enumerate(values)])
        rows = con.execute(f"SELECT i, {clean_sql(steps, 'x', countries, tables)} FROM v ORDER BY i").fetchall()
        return [r[1] for r in rows]
    finally:
        con.close()
