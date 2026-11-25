"""Node repository: the only code that reads or writes MDMNodes.

`MemoryRepo` backs unit tests and local UI development (MDM_LOCAL=1).
`SqlRepo` (sqlrepo.py) runs the same operations as SQL on a SQL warehouse
(the app) or on Spark (jobs and pipelines).

Everything that can return many rows is paged (offset / limit) and every
subtree operation is set-based on the `path_key` prefix, so the app never
pulls a whole branch of a 300M-node hierarchy into memory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .model import K, NODE_PROPERTY_COLUMNS, clone, get_prop, now_iso, parse_ptype, path_key


class ConflictError(Exception):
    """A node changed since it was read (optimistic concurrency)."""


# A column filter / sort of the Edit Children grid: {"name": "Alpha2", "ptype": "String", "values": [...]}
ColumnSpec = dict


class NodeRepo(ABC):
    # -- reads -----------------------------------------------------------------
    @abstractmethod
    def get_many(self, uids: list[str], include_deleted: bool = False) -> dict[str, dict]: ...

    @abstractmethod
    def children(self, parent: str | dict, offset: int = 0, limit: int | None = None, q: str = "",
                 sort: ColumnSpec | None = None, filters: list[ColumnSpec] | None = None) -> list[dict]:
        """Live children ordered by Order, then UID (or by `sort`), optionally filtered by a
        name/UID substring `q` and by column `filters`; paged by offset/limit."""

    @abstractmethod
    def count_children(self, parent: str | dict, q: str = "", filters: list[ColumnSpec] | None = None) -> int: ...

    @abstractmethod
    def distinct_values(self, parent: str | dict, column: ColumnSpec, q: str = "", limit: int = 40) -> list[tuple]:
        """[(stored value as text, count)] of one column over the children — the first `limit`."""

    def first_children(self, max_parent_depth: int, per: int) -> tuple[list[dict], dict[str, int]]:
        """The first `per` children (in display order) of every node at depth <= max_parent_depth,
        and the number of live children of each such parent. Used to warm the UI cache."""
        out, totals, level = [], {}, [self.get(self.root_uid())] if self.get(self.root_uid()) else []
        for _ in range(max_parent_depth + 1):
            nxt = []
            for p in level:
                kids = self.children(p, limit=per)
                totals[p["uid"]] = self.count_children(p)
                out += kids
                nxt += kids
            level = nxt
        return out, totals

    def root_uid(self) -> str:
        from .model import K
        return K.ROOT

    @abstractmethod
    def subtree(self, uid: str, limit: int | None = None) -> list[dict]:
        """Live descendants of uid (not uid itself). Only for small branches — see subtree_stats."""

    def branch_shape(self, node: dict, levels: int) -> dict:
        """What an export of the branch needs to know without reading it: the NodeTypes found at
        each level below `node` (0 = the node itself, up to `levels`) and whether Child Link nodes take
        part. A Child Link counts with its origin's types (an export row shows the origin's values).
        Returns {"types": {level: [type uids]}, "links": bool}."""
        types, links = self._branch_types(node, levels)
        types.setdefault(0, set()).update(node["types"])
        return {"types": {k: sorted(v) for k, v in types.items()}, "links": links}

    def _branch_types(self, node: dict, levels: int) -> tuple[dict, bool]:
        """({relative level: set of type uids, Child Links counted with their origin's types}, any Child Link)."""
        from .links import is_link, target_of
        types: dict[int, set] = {}
        targets: list[tuple[int, str]] = []
        for n in self.subtree(node["uid"]):
            d = n["depth"] - node["depth"]
            if d <= levels:
                if is_link(n):
                    targets.append((d, target_of(n)))
                else:
                    types.setdefault(d, set()).update(n["types"])
        got = self.get_many(sorted({t for _, t in targets if t})) if targets else {}
        for d, t in targets:
            if t in got:
                types.setdefault(d, set()).update(got[t]["types"])
        return types, bool(targets)

    @abstractmethod
    def subtree_stats(self, node: dict) -> dict:
        """{count, locked, loaded, height} of the live descendants, computed set-based."""

    @abstractmethod
    def ancestors(self, node: dict) -> dict[str, dict]:
        """The node's ancestors keyed by uid (found by path_key prefixes, no index needed)."""

    @abstractmethod
    def by_types(self, type_uids: list[str], limit: int | None = None) -> list[dict]:
        """Live nodes that have any of these types."""

    @abstractmethod
    def by_kind(self, kind: str) -> list[dict]:
        """Live definition nodes of a kind (NodeType, PropertyType, Enum, Action)."""

    @abstractmethod
    def links_under(self, parent_uids: list[str]) -> list[dict]:
        """Live Child Link nodes whose parent is one of parent_uids."""

    @abstractmethod
    def with_child_links(self) -> list[dict]:
        """Live nodes whose old child_links array is not empty (release upgrade to Child Link nodes)."""

    @abstractmethod
    def search(self, text: str, limit: int = 50, under: dict | None = None) -> list[dict]:
        """Live nodes whose name contains `text` (or whose UID is `text`); `under`: only its descendants."""

    @abstractmethod
    def child_counts(self, uids: list[str], under: dict | None = None) -> dict[str, int]:
        """Number of live children per uid; `under` = their common parent (narrows the scan)."""

    # -- writes ----------------------------------------------------------------
    @abstractmethod
    def upsert(self, nodes: list[dict], user: str, expected: dict[str, str] | None = None) -> None:
        """Insert or replace whole nodes (never moves them: see move_subtree). `expected` maps
        uid -> modified_at read earlier; a mismatch raises ConflictError and nothing is written."""

    @abstractmethod
    def insert_missing(self, nodes: list[dict], user: str) -> int:
        """Insert nodes whose uid does not exist yet (seeding); returns the number inserted."""

    @abstractmethod
    def allocate(self, count: int) -> list[int]:
        """Reserve `count` new uid numbers from the UID Counter node."""

    def allocate_block(self, count: int) -> int:
        """Reserve `count` new uid numbers; returns the first (the block is start .. start + count - 1)."""
        nums = self.allocate(count)
        return nums[0] if nums else 0

    @abstractmethod
    def soft_delete_subtree(self, node: dict, include_self: bool, user: str) -> int:
        """Set is_deleted on the node's whole branch in one statement; returns rows changed."""

    @abstractmethod
    def move_subtree(self, node: dict, new_parent: dict, user: str) -> int:
        """Re-parent node and rewrite path / path_key / depth of its branch in one statement."""

    @abstractmethod
    def lift_children(self, node: dict, user: str) -> int:
        """Re-parent every child of node to node's parent (the node's segment is removed from the
        path of its whole branch) in one statement; returns rows changed. The node itself is untouched."""

    def hard_delete(self, uids: list[str]) -> None:
        """Physically remove rows (Delete Mode = Hard delete). History stays in Delta time travel."""
        raise NotImplementedError

    def get(self, uid: str, include_deleted: bool = False) -> dict | None:
        return self.get_many([uid], include_deleted).get(uid)

    def chain(self, uid: str, key: str | None = None) -> list[dict] | None:
        """The node and its ancestors, root first (None if the node does not exist). `key` is the node's
        path_key when the caller knows it (the UI does): the SQL repo then reads all of them in one statement."""
        n = self.get(uid)
        if not n:
            return None
        anc = self.ancestors(n)
        return [anc[u] for u in n["path"] if u in anc] + [n]

    def named(self, text: str, parent: dict | None = None, types: list[str] | None = None, exact: bool = True,
              offset: int = 0, limit: int = 40) -> list[dict]:
        """Nodes under `parent` (an Enum's values) or of any of `types` (a typed Link's targets) whose name is
        `text` (exact, any case) or contains it (exact=False; empty text = all), or whose UID is `text`."""
        t = (text or "").strip().lower()
        if parent is not None:
            pool = self.children(parent)
        else:
            pool = self.by_types(list(types or []))
        hits = [n for n in pool if (not t and not exact) or n["uid"].lower() == t
                or (n["name"].lower() == t if exact else t in n["name"].lower())]
        return hits[offset:offset + limit]

    def children_with_total(self, parent: str | dict, offset: int = 0, limit: int | None = None,
                            q: str = "") -> tuple[list[dict], int]:
        """One page of children and the number of all of them (the SQL repo: one statement)."""
        return self.children(parent, offset=offset, limit=limit, q=q), self.count_children(parent, q=q)

    def _node(self, parent: str | dict) -> dict | None:
        return parent if isinstance(parent, dict) else self.get(parent)


def sort_key(node: dict):
    so = node.get("sort_order")
    return (so is None, so if so is not None else 0, node.get("uid_num") or 0)


def column_text(node: dict, spec: ColumnSpec) -> list[str]:
    """Stored value(s) of a grid column as text; arrays give one entry per item, empty → ['']."""
    v = get_prop(node, spec["name"])
    if isinstance(v, list):
        return [str(x) for x in v] or [""]
    if v is None:
        return [""]
    if isinstance(v, bool):
        return ["TRUE" if v else "FALSE"]
    if isinstance(v, float) and v.is_integer() and parse_ptype(spec.get("ptype", ""))[0] != "Float":
        return [str(int(v))]
    return [str(v)]


def _matches(node: dict, q: str, filters: list[ColumnSpec] | None) -> bool:
    if q:
        ql = q.lower()
        if ql not in node["name"].lower() and q.upper() != node["uid"]:
            return False
    for f in filters or []:
        allowed = set(f.get("values") or [])
        if not allowed.intersection(column_text(node, f)):
            return False
    return True


def _sort_value(node: dict, spec: ColumnSpec):
    base = parse_ptype(spec.get("ptype", "String"))[0]
    v = get_prop(node, spec["name"])
    if isinstance(v, list):
        v = ", ".join(map(str, v))
    if base in ("Number", "Float"):
        try:
            return (v is None, float(v) if v is not None else 0.0)
        except (TypeError, ValueError):
            return (True, 0.0)
    return (v is None, str(v if v is not None else "").lower())


class MemoryRepo(NodeRepo):
    def __init__(self, nodes: list[dict]):
        self.nodes: dict[str, dict] = {n["uid"]: clone(n) for n in nodes}

    def _live(self):
        return (n for n in self.nodes.values() if not n["is_deleted"])

    def get_many(self, uids, include_deleted=False):
        out = {}
        for uid in uids:
            n = self.nodes.get(uid)
            if n and (include_deleted or not n["is_deleted"]):
                out[uid] = clone(n)
        return out

    def _kids(self, parent, q="", filters=None):
        uid = parent["uid"] if isinstance(parent, dict) else parent
        return [n for n in self._live() if n["parent_uid"] == uid and _matches(n, q, filters)]

    def children(self, parent, offset=0, limit=None, q="", sort=None, filters=None):
        kids = self._kids(parent, q, filters)
        if sort:
            kids.sort(key=lambda n: (_sort_value(n, sort), sort_key(n)), reverse=sort.get("dir", 1) < 0)
        else:
            kids.sort(key=sort_key)
        end = None if limit is None else offset + limit
        return [clone(n) for n in kids[offset:end]]

    def count_children(self, parent, q="", filters=None):
        return len(self._kids(parent, q, filters))

    def first_children(self, max_parent_depth, per):
        groups: dict[str, list[dict]] = {}
        for n in self._live():
            if n["parent_uid"] and n["depth"] <= max_parent_depth + 1:
                groups.setdefault(n["parent_uid"], []).append(n)
        out, totals = [], {}
        for pu, kids in groups.items():
            kids.sort(key=sort_key)
            totals[pu] = len(kids)
            out += [clone(k) for k in kids[:per]]
        return out, totals

    def distinct_values(self, parent, column, q="", limit=40):
        counts: dict[str, int] = {}
        for n in self._kids(parent):
            for v in column_text(n, column):
                if not q or q.lower() in v.lower():
                    counts[v] = counts.get(v, 0) + 1
        return sorted(counts.items(), key=lambda kv: (kv[0] != "", kv[0].lower()))[:limit]

    def subtree(self, uid, limit=None):
        out = [clone(n) for n in self._live() if uid in n["path"]]
        return out if limit is None else out[:limit]

    def subtree_stats(self, node):
        sub = [n for n in self._live() if node["uid"] in n["path"]]
        return {"count": len(sub), "locked": sum(1 for n in sub if n["locked"]),
                "loaded": sum(1 for n in sub if n["origin"] == "loaded"),
                "height": max([n["depth"] - node["depth"] for n in sub] or [0])}

    def ancestors(self, node):
        return self.get_many(node["path"])

    def by_types(self, type_uids, limit=None):
        want = set(type_uids)
        out = [clone(n) for n in sorted(self._live(), key=sort_key) if want.intersection(n["types"])]
        return out if limit is None else out[:limit]

    def by_kind(self, kind):
        from .model import kind_of
        return [clone(n) for n in sorted(self._live(), key=sort_key) if kind_of(n) == kind]

    def links_under(self, parent_uids):
        from .model import kind_of
        ps = set(parent_uids)
        return [clone(n) for n in sorted(self._live(), key=sort_key) if n["parent_uid"] in ps and kind_of(n) == "Link"]

    def with_child_links(self):
        return [clone(n) for n in sorted(self._live(), key=sort_key) if n.get("child_links")]

    def search(self, text, limit=50, under=None):
        t = text.lower()
        hits = [n for n in self._live() if (t in n["name"].lower() or t == n["uid"].lower())
                and (under is None or under["uid"] in n["path"])]
        return [clone(n) for n in sorted(hits, key=lambda n: (n["depth"], n["name"]))[:limit]]

    def child_counts(self, uids, under=None):
        want = set(uids)
        out = {u: 0 for u in uids}
        for n in self._live():
            if n["parent_uid"] in want:
                out[n["parent_uid"]] += 1
        return out

    def upsert(self, nodes, user, expected=None):
        expected = expected or {}
        for uid, ts in expected.items():
            cur = self.nodes.get(uid)
            if cur is None or cur["modified_at"] != ts:
                raise ConflictError(f"{uid} was changed by someone else — reload and try again")
        stamp = now_iso()
        for n in nodes:
            m = clone(n)
            m["modified_at"], m["modified_by"] = stamp, user
            self.nodes[m["uid"]] = m

    def insert_missing(self, nodes, user):
        added = 0
        for n in nodes:
            if n["uid"] not in self.nodes:
                self.nodes[n["uid"]] = clone(n)
                added += 1
        return added

    def soft_delete_subtree(self, node, include_self, user):
        stamp, done = now_iso(), 0
        for n in self.nodes.values():
            if not n["is_deleted"] and (node["uid"] in n["path"] or (include_self and n["uid"] == node["uid"])):
                n.update(is_deleted=True, modified_at=stamp, modified_by=user)
                done += 1
        return done

    def move_subtree(self, node, new_parent, user):
        old_prefix = node["path"] + [node["uid"]]
        new_path = new_parent["path"] + [new_parent["uid"]]
        stamp, done = now_iso(), 0
        for n in self.nodes.values():
            if n["uid"] == node["uid"]:
                n.update(parent_uid=new_parent["uid"], path=list(new_path), depth=len(new_path))
            elif n["path"][:len(old_prefix)] == old_prefix:
                p = new_path + n["path"][len(old_prefix) - 1:]
                n.update(path=p, depth=len(p))
            else:
                continue
            n.update(modified_at=stamp, modified_by=user)
            done += 1
        return done

    def lift_children(self, node, user):
        stamp, done, i = now_iso(), 0, len(node["path"])
        for n in self.nodes.values():
            if n["uid"] != node["uid"] and n["path"][:i + 1] == node["path"] + [node["uid"]]:
                p = n["path"][:i] + n["path"][i + 1:]
                n.update(path=p, depth=len(p), modified_at=stamp, modified_by=user,
                         parent_uid=node["parent_uid"] if n["parent_uid"] == node["uid"] else n["parent_uid"])
                done += 1
        return done

    def hard_delete(self, uids):
        for u in uids:
            self.nodes.pop(u, None)

    def allocate(self, count):
        counter = self.nodes[K.UID_COUNTER]
        start = int(counter["properties"]["next"])
        counter["properties"]["next"] = start + count
        return list(range(start, start + count))


def column_sql(spec: ColumnSpec) -> tuple[str, bool]:
    """SQL expression for a grid column of MDMNodes and whether it is an array."""
    name, base = spec["name"], parse_ptype(spec.get("ptype", "String"))[0]
    col = NODE_PROPERTY_COLUMNS.get(name)
    if col:
        if col in ("types", "allowed_child_types", "steward"):
            return col, True
        if col == "locked":
            return "CASE WHEN locked THEN 'TRUE' ELSE 'FALSE' END", False
        if col == "modified_at":
            return "CAST(modified_at AS STRING)", False
        return f"CAST({col} AS STRING)", False
    path = "$['" + name.replace("\\", "\\\\").replace("'", "\\'") + "']"
    if base == "Array":
        return f"try_variant_get(properties, \"{path}\", 'array<string>')", True
    if base == "Boolean":
        return (f"CASE WHEN try_variant_get(properties, \"{path}\", 'boolean') THEN 'TRUE' ELSE 'FALSE' END"), False
    return f"try_variant_get(properties, \"{path}\", 'string')", False


def as_params(prefix: str, values: list[Any]) -> tuple[str, dict]:
    names = [f"{prefix}{i}" for i in range(len(values))]
    return ", ".join(f":{n}" for n in names), dict(zip(names, values))
