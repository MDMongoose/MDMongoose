"""Loading source records into MDMNodes.

The load pipeline (pipelines/load.py) builds, for every Table node, a cleaned
staging snapshot `rec_<key>` (SDP, expectations, AUTO CDC FROM SNAPSHOT, SCD1).
`merge_records` then diffs that snapshot against the Table node's Record
children by source key + record hash and upserts only what changed. Values the
user marked UserDefined are never overwritten.
"""

from __future__ import annotations

import re
from typing import Any

from .matching.spec import MATCH_PROPS
from .model import K, clone, new_node, now_iso
from .repo import NodeRepo
from .uid import to_uid

LOAD_INCREMENTAL = to_uid(1302)  # LoadModes/incremental: rows missing from the source are kept


def dataset_key(table_node: dict) -> str:
    """Stable, SQL-safe dataset suffix for a Table node: <name>_<uidnum>."""
    safe = re.sub(r"[^a-z0-9]+", "_", table_node["name"].lower()).strip("_") or "t"
    return f"{safe}_{table_node['uid_num']}"


TRANSFORMS = {
    "": lambda v: v, "none": lambda v: v, "—": lambda v: v,
    "trim()": lambda v: v.strip(), "upper()": lambda v: v.upper(), "lower()": lambda v: v.lower(),
    "upper(trim())": lambda v: v.strip().upper(), "title()": lambda v: v.title(),
}
DELETE_SOFT, DELETE_HARD, DELETE_KEEP = to_uid(1371), to_uid(1372), to_uid(1373)


def parse_mapping_entry(entry: str) -> tuple[str, str, str]:
    """'NAME1 => Legal Name | trim()' -> ('NAME1', 'Legal Name', 'trim()')."""
    src, _, rest = entry.partition("=>")
    prop, _, tr = rest.partition("|")
    return src.strip(), (prop.strip() or src.strip()), tr.strip()


def mapping_entry(src: str, prop: str, transform: str = "") -> str:
    return f"{src} => {prop}" + (f" | {transform}" if transform and transform not in ("—", "none") else "")


def table_config(table_node: dict) -> dict:
    p = table_node["properties"] or {}
    return {
        "uid": table_node["uid"], "key": dataset_key(table_node), "source": p.get("Table Name"),
        "keys": list(p.get("Keys") or []), "record_type": p.get("Record Type"),
        "expectations": list(p.get("Expectations") or []), "load_mode": p.get("Load Mode"),
        "name_expression": p.get("Name Expression") or "",
        "mapping": [parse_mapping_entry(e) for e in (p.get("Column Mapping") or []) if "=>" in e],
        "delete_mode": p.get("Delete Mode") or DELETE_SOFT, "schedule": p.get("Schedule") or "",
        "record_icon": p.get("Record Icon"), "run_golden": bool(p.get("Run Golden After Load")),
    }


def eval_name(expr: str, row: dict) -> str | None:
    """Tiny subset of SQL for the NAME expression: columns, 'literals', ||, trim/upper/lower()."""
    if not expr or not expr.strip():
        return None
    out = []
    for tok in re.split(r"\|\|", expr):
        t = tok.strip()
        m = re.fullmatch(r"'((?:[^']|'')*)'", t)
        if m:
            out.append(m.group(1).replace("''", "'"))
            continue
        m = re.fullmatch(r"(trim|upper|lower)\(\s*(\w+)\s*\)", t, re.I)
        col = m.group(2) if m else t
        v = row.get(col)
        if v is None:
            v = next((row[k] for k in row if k.lower() == col.lower()), None)
        v = "" if v is None else str(v)
        if m:
            v = {"trim": v.strip(), "upper": v.upper(), "lower": v.lower()}[m.group(1).lower()]
        out.append(v)
    s = "".join(out).strip()
    return s or None


def map_row(cfg: dict, data: dict) -> dict:
    """Source columns -> Record properties (Column Mapping, transforms). Without a mapping every
    column becomes a property with its own name."""
    if not cfg["mapping"]:
        return dict(data)
    props = {}
    for src, prop, tr in cfg["mapping"]:
        v = data.get(src)
        if v is not None:
            fn = TRANSFORMS.get(tr.lower(), TRANSFORMS[""])
            v = fn(str(v))
        props[prop] = v
    return props


def source_key(row: dict, keys: list[str]) -> str:
    return "|".join("" if row.get(k) is None else str(row.get(k)) for k in keys)


def _record_name(row: dict, key: str) -> str:
    for col, v in row.items():
        if "name" in col.lower() and v not in (None, ""):
            return f"{v}"
    return key


def record_name(cfg: dict, data: dict, key: str) -> str:
    return eval_name(cfg["name_expression"], data) or _record_name(data, key)


def merge_records(repo: NodeRepo, table_node: dict, rows: list[dict], user: str = "mdm_load",
                  full_snapshot: bool | None = None) -> dict:
    """rows: dicts of source columns plus `_record_hash`. Returns counts."""
    cfg = table_config(table_node)
    if full_snapshot is None:
        full_snapshot = cfg["load_mode"] != LOAD_INCREMENTAL
    if not cfg["keys"]:
        raise ValueError(f"Table '{table_node['name']}' has no Keys")
    existing = {n["source_key"]: n for n in repo.children(table_node["uid"]) if n["origin"] == "loaded"}
    types = [t for t in [cfg["record_type"], K.NT_RECORD] if t]
    # the stored hash covers the source row AND the mapping config, so a changed mapping or
    # NAME expression re-maps every record on the next load
    import hashlib
    fp = hashlib.sha256(repr((cfg["mapping"], cfg["name_expression"], types)).encode()).hexdigest()[:8]
    stamp = now_iso()
    new_rows, changed = [], []
    seen = set()
    for row in rows:
        h = f"{row.get('_record_hash')}:{fp}"
        data = {k: (None if v is None else str(v)) for k, v in row.items() if not k.startswith("_")}
        key = source_key(data, cfg["keys"])
        if key in seen:
            continue
        seen.add(key)
        props = map_row(cfg, data)
        cur = existing.get(key)
        if cur is None:
            new_rows.append((key, h, data, props))
        elif cur["record_hash"] != h:
            n = clone(cur)
            ud = n.get("user_defined") or {}
            # UserDefined values stay, and so does what matching wrote (Golden UID, Match Score, ...)
            kept = {k: v for k, v in (n["properties"] or {}).items() if ud.get(k) or k in MATCH_PROPS}
            n["properties"] = {**{k: v for k, v in props.items() if not ud.get(k) and k not in MATCH_PROPS}, **kept,
                               "Loaded At": stamp}
            if not ud.get("Name"):
                n["name"] = record_name(cfg, data, key)
            n["record_hash"] = h
            changed.append(n)
    nums = repo.allocate(len(new_rows)) if new_rows else []
    created = []
    for num, (key, h, data, props) in zip(nums, new_rows):
        n = new_node(num, record_name(cfg, data, key), types, table_node, origin="loaded",
                     source_table_uid=table_node["uid"], source_key=key, record_hash=h, icon=cfg["record_icon"],
                     max_child_depth=0, properties={**props, "Loaded At": stamp})
        created.append(n)
    gone = [n for k, n in existing.items() if k not in seen and not n["locked"]] if full_snapshot else []
    removed, hard = [], []
    if cfg["delete_mode"] == DELETE_HARD:
        hard = [n["uid"] for n in gone]
    elif cfg["delete_mode"] != DELETE_KEEP:
        removed = [dict(n, is_deleted=True) for n in gone]
    repo.upsert(created + changed + removed, user)
    if hard:
        repo.hard_delete(hard)
    return {"table": table_node["name"], "inserted": len(created), "updated": len(changed),
            "deleted": len(removed) + len(hard), "unchanged": len(seen) - len(created) - len(changed)}
