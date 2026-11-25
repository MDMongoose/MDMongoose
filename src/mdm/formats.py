"""Parse and format property values by PropertyType.

The same rules as the Edit Children grid: Enum/Link by Name or UID, arrays as
comma-separated lists, booleans as TRUE/FALSE, many timestamp formats.
The React UI sends cell text as typed or pasted; the backend parses and
validates every value with this module.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from .model import parse_ptype

# resolve(base, arg, text) -> (uid | None, error | None)
Resolver = Callable[[str, str, str], tuple[str | None, str | None]]
# name_of(uid) -> display name
Namer = Callable[[str], str]

TRUE_WORDS = {"true", "yes", "y", "1", "x", "✓", "igen", "i"}
FALSE_WORDS = {"false", "no", "n", "0", "-", "nem"}

MONTHS: dict[str, int] = {}
for _i, _names in enumerate([
    ["jan", "january", "januar", "jänner", "január", "janv"],
    ["feb", "february", "februar", "február", "févr"],
    ["mar", "march", "märz", "maerz", "március", "márc", "mars"],
    ["apr", "april", "április", "ápr", "avr"],
    ["may", "mai", "május", "máj"],
    ["jun", "june", "juni", "június", "jún", "juin"],
    ["jul", "july", "juli", "július", "júl", "juil"],
    ["aug", "august", "augusztus", "août"],
    ["sep", "sept", "september", "szeptember", "szept"],
    ["oct", "okt", "october", "oktober", "október"],
    ["nov", "november"],
    ["dec", "dez", "december", "dezember", "déc"],
]):
    for _n in _names:
        MONTHS[_n] = _i + 1


class ParseResult:
    __slots__ = ("ok", "value", "error", "note")

    def __init__(self, ok: bool, value: Any = None, error: str | None = None, note: str | None = None):
        self.ok, self.value, self.error, self.note = ok, value, error, note

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ParseResult(ok={self.ok}, value={self.value!r}, error={self.error!r}, note={self.note!r})"


def split_list(text: str) -> list[str]:
    """Comma/semicolon separated, with "double quotes" around values containing commas."""
    out, cur, quoted, i = [], "", False, 0
    s = str(text)
    while i < len(s):
        ch = s[i]
        if quoted:
            if ch == '"' and i + 1 < len(s) and s[i + 1] == '"':
                cur += '"'
                i += 1
            elif ch == '"':
                quoted = False
            else:
                cur += ch
        elif ch == '"':
            quoted = True
        elif ch in ",;":
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur.strip())
    return [x for x in out if x != ""]


def join_list(items: list[Any]) -> str:
    out = []
    for item in items:
        s = str(item)
        out.append('"' + s.replace('"', '""') + '"' if re.search(r'[,;"]', s) else s)
    return ", ".join(out)


def empty_of(base: str) -> Any:
    if base == "Array":
        return []
    if base == "Boolean":
        return False
    return None


def _ts(y, mo, d, h=0, mi=0, se=0, note=None) -> ParseResult:
    y, mo, d, h, mi, se = int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(se or 0)
    if y < 100:
        y += 2000 if y < 70 else 1900
    try:
        dt = datetime(y, mo, d, h, mi, se)
    except ValueError:
        return ParseResult(False, error="not a valid date/time")
    fmt = "%Y-%m-%d %H:%M:%S" if se else "%Y-%m-%d %H:%M"
    return ParseResult(True, dt.strftime(fmt), note=note)


def _from_dt(dt: datetime, note: str) -> ParseResult:
    return _ts(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, note)


def _split_time(t: str) -> tuple[str, int, int, int]:
    m = re.match(r"^(.*?)[\sT,]+(\d{1,2})(?::(\d{2}))?(?::(\d{2})(?:[.,]\d+)?)?\s*(am|pm|a\.m\.|p\.m\.)?$", t, re.I)
    if not m or (not m.group(3) and not m.group(5)):
        return t, 0, 0, 0
    h = int(m.group(2))
    if m.group(5):
        pm = m.group(5).lower().startswith("p")
        h = (12 if pm else 0) if h == 12 else (h + 12 if pm else h)
    return m.group(1).strip(), h, int(m.group(3) or 0), int(m.group(4) or 0)


def _month(word: str) -> int | None:
    w = word.rstrip(".")
    return MONTHS.get(w) or MONTHS.get(w[:3])


def parse_timestamp(raw: str) -> ParseResult:
    t = re.sub(r"\s+", " ", str(raw).strip())
    if re.fullmatch(r"\d{5}(\.\d+)?", t) and 20000 < float(t) < 80000:  # Excel serial (1900 system)
        secs = round((float(t) - 25569) * 86400)
        return _from_dt(datetime.fromtimestamp(secs, tz=timezone.utc), "Excel serial date")
    if re.fullmatch(r"\d{10}", t):
        return _from_dt(datetime.fromtimestamp(int(t), tz=timezone.utc), "Unix seconds (UTC)")
    if re.fullmatch(r"\d{13}", t):
        return _from_dt(datetime.fromtimestamp(int(t) / 1000, tz=timezone.utc), "Unix milliseconds (UTC)")
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(?:[ T]?(\d{2})(\d{2})(\d{2})?)?", t)
    if m:
        return _ts(m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6))
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})", t, re.I)
    if m:
        off = m.group(7).upper()
        iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}:{m.group(5)}:{m.group(6) or '00'}"
        iso += "+00:00" if off == "Z" else (off if ":" in off else off[:3] + ":" + off[3:])
        try:
            dt = datetime.fromisoformat(iso).astimezone(timezone.utc)
        except ValueError:
            return ParseResult(False, error="not a valid date/time")
        return _from_dt(dt, "offset converted to UTC")
    d, h, mi, se = _split_time(t)
    d = d.rstrip(".").strip()
    m = re.fullmatch(r"(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})", d)
    if m:
        return _ts(m.group(1), m.group(2), m.group(3), h, mi, se)
    m = re.fullmatch(r"(\d{1,2})\s*([-/.])\s*(\d{1,2})\s*[-/.]\s*(\d{2}|\d{4})", d)
    if m:
        a, sep, b, y = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
        if sep == "." or a > 12:
            return _ts(y, b, a, h, mi, se)
        if b > 12:
            return _ts(y, a, b, h, mi, se, "month-first (US) detected")
        return _ts(y, b, a, h, mi, se, "ambiguous — read day-first (DD/MM)")
    low = re.sub(r"\s+", " ", d.lower().replace(",", " ")).strip()
    m = re.fullmatch(r"(\d{1,2})\.?[ -]([^\W\d_.]+\.?)[ -](\d{2}|\d{4})", low)
    if m and _month(m.group(2)):
        return _ts(m.group(3), _month(m.group(2)), m.group(1), h, mi, se)
    m = re.fullmatch(r"(?:[^\W\d_]+\.? )?([^\W\d_.]+\.?) (\d{1,2})(?:st|nd|rd|th)?\.? (\d{4})", low)
    if m and _month(m.group(1)):
        return _ts(m.group(3), _month(m.group(1)), m.group(2), h, mi, se)
    m = re.fullmatch(r"(\d{4})\.? ([^\W\d_.]+\.?) (\d{1,2})\.?", low)
    if m and _month(m.group(2)):
        return _ts(m.group(1), _month(m.group(2)), m.group(3), h, mi, se)
    return ParseResult(False, error="unrecognised date — e.g. 2026-11-15 09:30, 15.11.2026, 11/15/2026, 15 Nov 2026")


ICONS = {"box", "folder", "globe", "cube", "table", "file", "building", "star", "sliders", "tag", "lock",
         "bolt", "hash", "list", "shield", "calendar", "flag", "pin", "server", "book", "user", "users", "truck", "wrench", "leaf", "download"}


def parse_value(ptype: str, text: Any, resolve: Resolver | None = None) -> ParseResult:
    """Parse user text (typed or pasted) into the stored value for a PropertyType."""
    base, arg = parse_ptype(ptype)
    t = "" if text is None else str(text).strip()
    if t == "":
        return ParseResult(True, empty_of(base))
    if base == "Number":
        n = re.sub(r"[\s  _]", "", t)
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", n):
            n = n.replace(",", "")
        return ParseResult(True, int(n)) if re.fullmatch(r"-?\d+", n) else ParseResult(False, error="not a whole number")
    if base == "Float":
        f = re.sub(r"[\s  ]", "", t)
        f = f.replace(",", "") if ("," in f and "." in f) else f.replace(",", ".", 1)
        return ParseResult(True, float(f)) if re.fullmatch(r"-?\d+(\.\d+)?", f) else ParseResult(False, error="not a number")
    if base == "Timestamp":
        return parse_timestamp(t)
    if base == "Boolean":
        b = t.lower()
        if b in TRUE_WORDS:
            return ParseResult(True, True)
        if b in FALSE_WORDS:
            return ParseResult(True, False)
        return ParseResult(False, error="use TRUE / FALSE")
    if base == "Icon":
        return ParseResult(True, t.lower()) if t.lower() in ICONS else ParseResult(False, error="unknown icon")
    if base in ("Enum", "Link"):
        if resolve is None:
            return ParseResult(False, error="no resolver")
        uid, err = resolve(base, arg or "Node", t)
        return ParseResult(True, uid) if uid else ParseResult(False, error=f"{err} in {arg}")
    if base == "Array":
        items = split_list(t)
        if arg in ("String", "OwnProperty"):
            return ParseResult(True, items)
        if resolve is None:
            return ParseResult(False, error="no resolver")
        ids, errs = [], []
        for item in items:
            uid, err = resolve("Array", arg or "Node", item)
            if uid:
                if uid not in ids:
                    ids.append(uid)
            else:
                errs.append(err)
        return ParseResult(False, error="; ".join(errs) + f" in {arg}") if errs else ParseResult(True, ids)
    if base == "UID":
        return ParseResult(False, error="read-only")
    return ParseResult(True, t)


def format_value(ptype: str, value: Any, name_of: Namer | None = None) -> str:
    """Stored value -> clipboard/display text (inverse of parse_value)."""
    base, arg = parse_ptype(ptype)
    if value is None:
        return ""
    if base == "Boolean":
        return "TRUE" if value else "FALSE"
    if base == "Timestamp":
        r = parse_timestamp(str(value))
        return r.value if r.ok else str(value)
    if base in ("Enum", "Link"):
        return name_of(value) if name_of else str(value)
    if base == "Array":
        if arg in ("String", "OwnProperty"):
            return join_list(value)
        return join_list([name_of(v) if name_of else v for v in value])
    return str(value)


def validate_value(ptype: str, value: Any) -> str | None:
    """Check an already-typed JSON value from the UI. Returns an error or None."""
    base, arg = parse_ptype(ptype)
    if value is None:
        return None
    if base == "Number" and not (isinstance(value, int) and not isinstance(value, bool)):
        return "not a whole number"
    if base == "Float" and not (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return "not a number"
    if base == "Boolean" and not isinstance(value, bool):
        return "not TRUE/FALSE"
    if base == "Array" and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        return "not a list"
    if base in ("Enum", "Link", "String", "Icon", "Timestamp", "Expression", "OwnProperty") and not isinstance(value, str):
        return "not text"
    if base == "Timestamp" and not parse_timestamp(value).ok:
        return "not a valid timestamp"
    return None
