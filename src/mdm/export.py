"""Export action: subtree -> exploded table (L0..Ln) -> csv or xlsx.

Like Edit Children: one row per deepest node; L0 (the node itself) and the
parents repeat on every row. Each level's columns are the union of the
properties of the NodeTypes on that level. Balanced fills levels below a node
without children by cloning that node; None leaves them empty.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from .catalog import Catalog
from .flatten import Tree, explode
from .formats import format_value
from .model import get_prop
from .repo import NodeRepo

BASE_COLS = ("UID", "Name", "Types")
PREVIEW_PER_PARENT = 40
APP_EXPORT_LIMIT = 200_000  # larger branches are exported by the mdm_core job into the exports volume
LEVEL_LABEL = {0: "the node itself", 1: "children", 2: "their children"}


@dataclass
class ExportOptions:
    format: str = "xlsx"
    depth: int = 1
    balanced: bool = False
    with_props: bool = True
    with_uid: bool = True
    mark_balanced: bool = False
    file_name: str = ""
    delimiter: str = ","
    bom: bool = True
    sheets: str = "one"  # one | per_level
    header: str = "styled"  # styled | plain
    extra: dict = field(default_factory=dict)


def build(repo: NodeRepo, catalog: Catalog, node: dict, opt: ExportOptions, preview: bool = False):
    """Returns (groups, columns, rows). Each row cell: {text, kind} where kind is
    '' | 'clone' | 'empty' | 'undefined' | 'repeated'."""
    # a preview reads at most PREVIEW_PER_PARENT children per node, on demand
    tree = Tree(repo, [node], preload=not preview, per_parent=PREVIEW_PER_PARENT if preview else None,
                link_uid="origin")   # a Child Link row shows its origin's UID and values
    raw = explode(tree, [node], max(0, int(opt.depth)), False)
    # keep only the levels that occur, like the Export dialog (depth larger than the tree is fine)
    n_lev = max((sum(1 for x in r if x is not None) for r in raw), default=1)
    raw = [r[:n_lev] for r in raw]
    name_of = catalog.namer(tree.nodes)
    levels = []
    for L in range(n_lev):
        types: dict[str, int] = {}
        props: dict[tuple, dict] = {}
        seen = set()
        for r in raw:
            n = r[L]
            if n is None or n["uid"] in seen:
                continue
            seen.add(n["uid"])
            for t in n["types"]:
                types[t] = types.get(t, 0) + 1
            if opt.with_props:
                for d in catalog.property_defs(n):
                    if d["name"] in BASE_COLS or d["defined_by"] == ["Node"]:
                        continue  # base Node properties (Origin, Icon, Steward…) are not exported
                    k = (d["name"], d["ptype"])
                    if k not in props:
                        props[k] = {"name": d["name"], "ptype": d["ptype"], "defined_by": list(d["defined_by"]),
                                    "priority": d["priority"]}
                    else:
                        for t in d["defined_by"]:
                            if t not in props[k]["defined_by"]:
                                props[k]["defined_by"].append(t)
        levels.append({"types": types, "props": sorted(props.values(), key=lambda p: p["priority"])})
    cols, groups = [], []
    for L, lv in enumerate(levels):
        start = len(cols)
        if opt.with_uid:
            cols.append({"L": L, "f": "uid", "label": f"L{L}.UID", "ptype": "UID"})
        cols.append({"L": L, "f": "name", "label": f"L{L}.Name", "ptype": "String"})
        cols.append({"L": L, "f": "types", "label": f"L{L}.Types", "ptype": "Array(NodeType)"})
        for p in lv["props"]:
            cols.append({"L": L, "f": "prop", "name": p["name"], "label": f"L{L}.{p['name']}", "ptype": p["ptype"],
                         "defined_by": p["defined_by"], "shared": len(p["defined_by"]) > 1})
        tnames = " ∪ ".join(name_of(t) for t in lv["types"]) or "—"
        groups.append({"span": len(cols) - start,
                       "label": f"L{L} · {LEVEL_LABEL.get(L, 'below')} — {tnames}", "level": L})
    rows = []
    prev = None
    for r in raw:
        last = max(i for i, x in enumerate(r) if x is not None)
        any_clone = False
        cells = []
        for c in cols:
            n, kind = r[c["L"]], ""
            if n is None and opt.balanced:
                n, kind = r[last], "clone"
            if n is None:
                cells.append({"text": "", "kind": "empty"})
                continue
            if kind == "clone":
                any_clone = True
            if c["f"] == "prop":
                defs = {(d["name"], d["ptype"]) for d in catalog.property_defs(n)}
                if (c["name"], c["ptype"]) not in defs:
                    cells.append({"text": "", "kind": "undefined"})
                    continue
                text = format_value(c["ptype"], get_prop(n, c["name"]), name_of)
            elif c["f"] == "uid":
                text = n["uid"]
            elif c["f"] == "name":
                text = n["name"]
            else:
                text = ", ".join(name_of(t) for t in n["types"])
            if not kind and prev is not None and prev[c["L"]] is n and c["L"] < last:
                kind = "repeated"
            cells.append({"text": text, "kind": kind})
        if opt.mark_balanced:
            cells.append({"text": "TRUE" if any_clone else "FALSE", "kind": "flag"})
        rows.append(cells)
        prev = r
    if opt.mark_balanced:
        cols.append({"L": None, "f": "flag", "label": "Balanced", "ptype": "Boolean"})
        groups.append({"span": 1, "label": "", "level": None})
    return groups, cols, rows


def preview(repo, catalog, node, opt: ExportOptions, limit: int = 60) -> dict:
    groups, cols, rows = build(repo, catalog, node, opt, preview=True)
    clones = sum(1 for r in rows for c, col in zip(r, cols) if c["kind"] == "clone" and col["f"] == "name")
    return {"groups": groups, "columns": [{k: v for k, v in c.items()} for c in cols], "rows": rows[:limit],
            "total_rows": len(rows), "levels": len([g for g in groups if g["level"] is not None]),
            "clones": clones, "ragged": any(c["kind"] == "empty" for r in rows for c in r)}


def to_csv(header, rows, delimiter=",", bom=True) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter)
    w.writerow(header)
    w.writerows(rows)
    return (("﻿" if bom else "") + buf.getvalue()).encode("utf-8")


def to_xlsx(sheets: list[tuple[str, list[str], list[list[str]]]], styled: bool = True) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    wb.remove(wb.active)
    for title, header, rows in sheets:
        ws = wb.create_sheet((title or "Export")[:31])
        ws.append(header)
        if styled:
            for c in ws[1]:
                c.font = Font(bold=True)
                c.fill = PatternFill("solid", fgColor="EEF1F5")
            ws.freeze_panes = "A2"
            if header:
                ws.auto_filter.ref = ws.dimensions
        for r in rows:
            ws.append(r)
        for i, h in enumerate(header, start=1):
            width = max([len(h)] + [len(str(r[i - 1])) for r in rows[:200]])
            ws.column_dimensions[ws.cell(1, i).column_letter].width = min(60, max(8, width + 2))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def run(repo: NodeRepo, catalog: Catalog, node: dict, fmt: str = "csv", depth: int = 1,
        balanced: bool = False, opt: ExportOptions | None = None) -> tuple[str, bytes, str]:
    """Returns (filename, content, media_type)."""
    opt = opt or ExportOptions(format=fmt, depth=depth, balanced=balanced)
    groups, cols, rows = build(repo, catalog, node, opt)
    header = [c["label"] for c in cols]
    data = [[c["text"] for c in r] for r in rows]
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in node["name"]) or node["uid"]
    base = (opt.file_name or safe).rsplit(".", 1)[0] if opt.file_name else safe
    if opt.format == "xlsx":
        if opt.sheets == "per_level":
            sheets = []
            for g in groups:
                if g["level"] is None:
                    continue
                idx = [i for i, c in enumerate(cols) if c["L"] == g["level"]]
                seen, lv_rows = set(), []
                for r in data:
                    key = tuple(r[i] for i in idx)
                    if any(key) and key not in seen:
                        seen.add(key)
                        lv_rows.append(list(key))
                sheets.append((f"L{g['level']}", [header[i] for i in idx], lv_rows))
        else:
            sheets = [(node["name"], header, data)]
        return (f"{base}.xlsx", to_xlsx(sheets, opt.header != "plain"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return f"{base}.csv", to_csv(header, data, opt.delimiter, opt.bom), "text/csv; charset=utf-8"
