"""Exploded (spark-explode-like) view of a subtree, shared by Edit Children and Export.

Level 0 is the top node(s); each level adds the union of the properties of all
nodes on that level as columns. A parent with several children is repeated on
one row per child. Balanced mode fills missing lower levels by repeating the
last node, so every row reaches the same depth.
"""

from __future__ import annotations

from .catalog import Catalog
from .formats import format_value
from .links import as_row, is_link, target_of
from .model import get_prop
from .repo import NodeRepo, sort_key

SKIP_IN_GRID = {"Parent", "Last Modified", "Origin"}


class Tree:
    """Children lookup; child links are resolved in place.

    preload=True reads the whole branch once (exports of bounded size). preload=False reads
    children per node on demand, at most `per_parent` each (the paged grid and previews)."""

    def __init__(self, repo: NodeRepo, roots: list[dict], preload: bool = True, per_parent: int | None = None,
                 link_uid: str = "own"):
        self.repo, self.per_parent, self.link_uid = repo, per_parent, link_uid
        self.kids: dict[str, list[dict]] = {}
        self.truncated: set[str] = set()
        if preload:
            for r in roots:
                for n in repo.subtree(r["uid"]):
                    self.kids.setdefault(n["parent_uid"], []).append(n)
            for v in self.kids.values():
                v.sort(key=sort_key)
        self.nodes: dict[str, dict] = {n["uid"]: n for v in self.kids.values() for n in v}
        self.nodes.update({r["uid"]: r for r in roots})

    def children(self, node: dict) -> list[dict]:
        """The node's children; a Child Link child shows its origin's values (see links.as_row) and has its own
        children. link_uid "own" keeps the link node's UID (the grid edits it), "origin" shows the origin's."""
        key = node.get("_via") or node["uid"]
        if key not in self.kids:
            lim = self.per_parent
            got = self.repo.children(key, limit=lim + 1 if lim else None)
            if lim and len(got) > lim:
                got = got[:lim]
                self.truncated.add(key)
            self.kids[key] = got
            self.nodes.update({n["uid"]: n for n in got})
        out = list(self.kids[key])
        targets = [t for n in out if (t := target_of(n))]
        if targets:
            origins = self.repo.get_many(targets)
            self.nodes.update(origins)
            out = [as_row(n, origins.get(target_of(n)), self.link_uid == "own") if is_link(n) else n for n in out]
        return out


def explode(tree: Tree, tops: list[dict], levels: int, balanced: bool) -> list[list[dict | None]]:
    """Rows of nodes, one entry per level (0..levels)."""
    def rows_of(node: dict, lvl: int) -> list[list[dict | None]]:
        if lvl == levels:
            return [[node]]
        kids = tree.children(node)
        if not kids:
            fill = [node] * (levels - lvl) if balanced else [None] * (levels - lvl)
            return [[node] + fill]
        out = []
        for k in kids:
            for r in rows_of(k, lvl + 1):
                out.append([node] + r)
        return out

    rows = []
    for t in tops:
        rows += rows_of(t, 0)
    return rows


def level_columns(catalog: Catalog, rows, levels: int, skip=frozenset()) -> list[list[dict]]:
    cols: list[list[dict]] = []
    for lvl in range(levels + 1):
        seen: dict[tuple, dict] = {}
        done = set()
        for r in rows:
            n = r[lvl]
            if n is None or n["uid"] in done:
                continue
            done.add(n["uid"])
            for d in catalog.property_defs(n):
                if d["name"] in skip:
                    continue
                key = (d["name"], d["ptype"])
                if key not in seen:
                    seen[key] = {"level": lvl, "name": d["name"], "ptype": d["ptype"], "group": d["group"],
                                 "priority": d["priority"], "defined_by": list(d["defined_by"])}
                else:
                    for t in d["defined_by"]:
                        if t not in seen[key]["defined_by"]:
                            seen[key]["defined_by"].append(t)
        lvl_cols = sorted(seen.values(), key=lambda c: (c["name"] != "UID", c["name"] != "Name", c["priority"]))
        names = [c["name"] for c in lvl_cols]
        for c in lvl_cols:  # the same name with two PropertyTypes gets the type in its key
            c["key"] = f"L{lvl}.{c['name']}" + (f" ({c['ptype']})" if names.count(c["name"]) > 1 else "")
        cols.append(lvl_cols)
    return cols


def cell(catalog: Catalog, node: dict | None, col: dict, name_of) -> str:
    if node is None:
        return ""
    names = {d["name"]: d["ptype"] for d in catalog.property_defs(node)}
    if names.get(col["name"]) != col["ptype"]:
        return ""
    return format_value(col["ptype"], get_prop(node, col["name"]), name_of)


def flatten(repo: NodeRepo, catalog: Catalog, tops: list[dict], levels: int, balanced: bool = False,
            skip=frozenset(), roots_for_tree: list[dict] | None = None, preload: bool = True,
            per_parent: int | None = None, extra_types: list[list[str]] | None = None, link_uid: str = "own"):
    """extra_types: type lists that must contribute columns to level 0 even if no loaded row has
    them (the grid pages through children, but its columns cover all of them)."""
    tree = Tree(repo, roots_for_tree or tops, preload, per_parent, link_uid)
    rows = explode(tree, tops, levels, balanced)
    fake = [[{"uid": f"__t{i}", "types": t, "properties": {}}] + [None] * levels for i, t in enumerate(extra_types or [])]
    cols = level_columns(catalog, rows + fake, levels, skip)
    name_of = catalog.namer(tree.nodes)
    flat_cols = [c for lvl in cols for c in lvl]
    out_rows = []
    for r in rows:
        values = {c["key"]: cell(catalog, r[c["level"]], c, name_of) for c in flat_cols}
        undefined = []
        for c in flat_cols:
            n = r[c["level"]]
            if n is not None and (c["name"], c["ptype"]) not in {(d["name"], d["ptype"]) for d in catalog.property_defs(n)}:
                undefined.append(c["key"])
        out_rows.append({
            "uids": [n["uid"] if n else None for n in r],
            "links": [bool(n and n.get("_link")) for n in r],
            "modified": [n["modified_at"] if n else None for n in r],
            "user_defined": [dict(n.get("user_defined") or {}) if n else {} for n in r],
            "values": values, "undefined": undefined,
            "truncated": [bool(n and (n.get("_via") or n["uid"]) in tree.truncated) for n in r],
        })
    return flat_cols, out_rows
