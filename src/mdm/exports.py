"""Export nodes: saved exports of a branch, refreshed by the mdm_export job.

An Export node (NodeType Export, under Model / Exports) describes the same exploded table as the
Export dialog — L0 is the Root Node, every level adds its UID, Name, Types and properties — and
where it goes:

  Materialized view  mdm_export.<Object Name>, declared by the mdm_export pipeline (refreshed
                     incrementally by Databricks when the query allows it)
  View               mdm_export.<Object Name>, created by the app when the node is saved
  CSV / Excel        /Volumes/<catalog>/mdm_export/exports/<Object Name>.csv|.xlsx, written by the job

Columns use `_` instead of `.`: L0_UID, L0_Name, L1_Legal_Name …
The SQL is generated from the branch's NodeTypes per level (branch_shape), so it reads MDMNodes
only through joins on uid / parent — no recursion — which keeps materialized views incremental.
"""

from __future__ import annotations

import re
from typing import Any

from . import names
from .catalog import Catalog
from .export import BASE_COLS, ExportOptions
from .hierarchy import RuleError
from .model import NODE_PROPERTY_COLUMNS, K, parse_ptype
from .repo import NodeRepo
from .uid import is_uid

KINDS = {"materialized view": "mv", "view": "view", "excel": "excel", "csv": "csv"}
KIND_LABEL = {"mv": "Materialized view", "view": "View", "excel": "Excel", "csv": "CSV"}
RESERVED = {"nodes", "node_index", "node_edges", "node_history", "load_tables"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
MAX_DEPTH = 10
SQL_TYPE = {"Number": "bigint", "Float": "double", "Boolean": "boolean", "Timestamp": "timestamp"}


def default_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "export"
    return s if s[0].isalpha() else f"x_{s}"


def spec_of(node: dict, name_of) -> dict:
    """The export settings of an Export node, with defaults."""
    p = node.get("properties") or {}
    label = lambda k: (name_of(p[k]) if p.get(k) else "").strip().lower()  # noqa: E731
    try:
        depth = int(p.get("Child Depth") if p.get("Child Depth") not in (None, "") else 1)
    except (TypeError, ValueError):
        depth = 1
    return {
        "kind": KINDS.get(label("Export Type")),
        "name": (p.get("Object Name") or "").strip() or default_name(node["name"]),
        "root": p.get("Root Node"),
        "depth": depth,
        "balanced": label("Balanced") == "balanced",
        "with_props": p.get("Include Properties", True) is not False,
        "with_uid": p.get("Include UIDs", True) is not False,
        "mark_balanced": p.get("Balanced Flag") is True,
        "delimiter": p.get("CSV Delimiter") or ",",
        "sheets": "per_level" if label("Excel Sheets") == "sheet per level" else "one",
        "header": "plain" if label("Excel Header") == "plain" else "styled",
    }


def location(spec: dict, catalog_name: str) -> str:
    if spec["kind"] in ("mv", "view"):
        return f"{catalog_name}.{names.EXPORT}.{spec['name']}"
    ext = "xlsx" if spec["kind"] == "excel" else "csv"
    return f"{names.volume_path(catalog_name)}/{spec['name']}.{ext}"


def validate(spec: dict, node_uid: str, others: list[dict], name_of) -> None:
    """Checks that need no SQL: type, name format, reserved names, other Export nodes, depth."""
    if not spec["kind"]:
        raise RuleError("choose an Export Type: Materialized view, View, Excel or CSV")
    if not NAME_RE.match(spec["name"]):
        raise RuleError(f"Object Name '{spec['name']}' must start with a letter and use only a-z, 0-9 and _")
    if spec["kind"] in ("mv", "view") and spec["name"] in RESERVED:
        raise RuleError(f"'{spec['name']}' is a system object of {names.EXPORT} — choose another Object Name")
    if not spec["root"]:
        raise RuleError("set the Root Node — the branch to export")
    if not 0 <= spec["depth"] <= MAX_DEPTH:
        raise RuleError(f"Child Depth must be between 0 and {MAX_DEPTH}")
    if spec["kind"] == "csv" and len(spec["delimiter"]) != 1:
        raise RuleError("CSV Delimiter must be one character")
    table_like = spec["kind"] in ("mv", "view")
    for o in others:
        if o["uid"] == node_uid:
            continue
        os_ = spec_of(o, name_of)
        if os_["name"] == spec["name"] and ((os_["kind"] in ("mv", "view")) == table_like):
            raise RuleError(f"the Export node '{o['name']}' ({o['uid']}) already uses the name {spec['name']}")


# ------------------------------------------------------------------------------- SQL
def _lit(s: str) -> str:
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _path(name: str) -> str:
    return _lit("$['" + name.replace("\\", "\\\\").replace("'", "\\'") + "']")


def _col(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", label).strip("_")


def level_props(catalog: Catalog, type_uids: list[str]) -> list[dict]:
    """Union of the properties of the NodeTypes on one level, like the Export dialog."""
    props: dict[tuple, dict] = {}
    for t in type_uids:
        for d in catalog.property_defs({"types": [t]}):
            if d["name"] in BASE_COLS or d["defined_by"] == ["Node"]:
                continue
            k = (d["name"], d["ptype"])
            if k not in props:
                props[k] = {"name": d["name"], "ptype": d["ptype"], "priority": d["priority"]}
    return sorted(props.values(), key=lambda p: p["priority"])


def build_sql(repo: NodeRepo, catalog: Catalog, spec: dict, nodes_table: str) -> tuple[str, list[str]]:
    """SELECT of the exploded branch. Returns (sql, column names)."""
    root = repo.get(spec["root"]) if spec.get("root") else None
    if not root:
        raise RuleError("the Root Node does not exist")
    catalog.refresh()
    shape = repo.branch_shape(root, spec["depth"])
    levels = 0
    for k in range(spec["depth"] + 1):
        if shape["types"].get(k):
            levels = k
    in_branch = {t for k in range(levels + 1) for t in shape["types"].get(k, [])}
    type_map = ", ".join(f"{_lit(t['uid'])}, {_lit(t['name'])}" for t in catalog.types.values()
                         if t["uid"] in in_branch)
    type_map = f"map({type_map})" if type_map else "map()"

    refs: dict[tuple, str] = {}
    joins: list[str] = []

    def value(a: str, p: dict) -> str:
        name, ptype = p["name"], p["ptype"]
        base, arg = parse_ptype(ptype)
        col = NODE_PROPERTY_COLUMNS.get(name)
        if col:
            return f"{a}.{col}"
        raw = f"try_variant_get({a}.properties, {_path(name)}, 'string')"
        if base in ("Enum", "Link"):
            key = (a, name)
            if key not in refs:
                refs[key] = f"r{len(refs) + 1}"
                joins.append(f"LEFT JOIN x AS {refs[key]} ON {refs[key]}.uid = {raw}")
            return f"{refs[key]}.name"
        if base == "Array":
            arr = f"try_variant_get({a}.properties, {_path(name)}, 'array<string>')"
            if arg == "NodeType":
                arr = f"transform({arr}, t -> coalesce(element_at({type_map}, t), t))"
            return f"array_join({arr}, ', ')"
        if base in SQL_TYPE:
            return f"try_variant_get({a}.properties, {_path(name)}, '{SQL_TYPE[base]}')"
        return raw

    def field(a: str, f: str, p: dict | None = None) -> str:
        if f == "uid":
            return f"{a}.uid"
        if f == "name":
            return f"{a}.name"
        if f == "types":
            return f"array_join(transform({a}.types, t -> coalesce(element_at({type_map}, t), t)), ', ')"
        return value(a, p)

    cols, used = [], set()

    def add(expr: str, label: str):
        name = _col(label) or "col"
        base, i = name, 2
        while name.lower() in used:
            name, i = f"{base}_{i}", i + 1
        used.add(name.lower())
        cols.append((expr, name))

    for k in range(levels + 1):
        fields = ([("uid", "UID", None)] if spec["with_uid"] else []) + [("name", "Name", None), ("types", "Types", None)]
        if spec["with_props"]:
            fields += [("prop", p["name"], p) for p in level_props(catalog, shape["types"].get(k, []))]
        for f, label, p in fields:
            if spec["balanced"] and k > 0:  # a missing level repeats the deepest node of the row
                whens = " ".join(f"WHEN n{j}.uid IS NOT NULL THEN {field(f'n{j}', f, p)}" for j in range(k, 0, -1))
                expr = f"CASE {whens} ELSE {field('n0', f, p)} END"
            else:
                expr = field(f"n{k}", f, p)
            add(expr, f"L{k}.{label}")
    if spec["mark_balanced"]:
        add(f"n{levels}.uid IS NULL" if spec["balanced"] and levels > 0 else "false", "Balanced")

    from .model import path_key
    branch = f" AND path_key LIKE {_lit(path_key(root) + '%')}"
    # a Child Link row shows its origin: the origin's UID, name, types and properties, the link's children
    edges = ("SELECT parent_uid AS _p, uid AS _id, uid, name, types, properties, sort_order FROM n" if not shape["links"] else
             "SELECT n.parent_uid AS _p, n.uid AS _id, coalesce(o.uid, n.uid) AS uid, coalesce(o.name, n.name) AS name,\n"
             "    coalesce(o.types, n.types) AS types, coalesce(o.properties, n.properties) AS properties, n.sort_order\n"
             f"  FROM n LEFT JOIN {nodes_table} AS o ON n.kind = 'Link' AND NOT o.is_deleted\n"
             f"    AND o.uid = try_variant_get(n.properties, {_path('Linked Node')}, 'string')")
    level_joins = [f"LEFT JOIN k AS n{j} ON n{j}._p = n{j - 1}.{'uid' if j == 1 else '_id'}" for j in range(1, levels + 1)]
    select = ",\n  ".join(f"{e} AS `{c}`" for e, c in cols)
    sql = (f"WITH n AS (\n  SELECT uid, parent_uid, name, types, properties, sort_order, kind\n"
           f"  FROM {nodes_table} WHERE NOT is_deleted{branch}\n),\n"
           f"k AS (\n  {edges}\n)" + (f",\nx AS (SELECT uid, name FROM {nodes_table} WHERE NOT is_deleted)\n" if joins else "\n") +
           f"SELECT\n  {select}\n"
           f"FROM n AS n0\n" + "".join(j + "\n" for j in level_joins + joins) +
           f"WHERE n0.uid = {_lit(root['uid'])}")
    return sql, [c for _, c in cols]


def view_ddl(sql: str, catalog_name: str, name: str, node: dict) -> str:
    return (f"CREATE OR REPLACE VIEW `{catalog_name}`.`{names.EXPORT}`.`{name}`\n"
            f"COMMENT {_lit('MDMongoose export ' + node['uid'] + ' — ' + node['name'])}\nAS {sql}")


def options(spec: dict) -> ExportOptions:
    return ExportOptions(format="xlsx" if spec["kind"] == "excel" else "csv", depth=spec["depth"],
                         balanced=spec["balanced"], with_props=spec["with_props"], with_uid=spec["with_uid"],
                         mark_balanced=spec["mark_balanced"], file_name=spec["name"], delimiter=spec["delimiter"],
                         sheets=spec["sheets"], header=spec["header"])


def export_nodes(repo: NodeRepo, uids: list[str] | None = None) -> list[dict]:
    nodes = repo.by_types([K.NT_EXPORT])
    if uids:
        nodes = [n for n in nodes if n["uid"] in uids]
    return [n for n in nodes if not n["is_deleted"]]


def is_export_uid(value: Any) -> bool:
    return isinstance(value, str) and is_uid(value)
