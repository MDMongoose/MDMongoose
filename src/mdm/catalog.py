"""Definitions loaded from the hierarchy itself: NodeTypes, Actions, Enums, permission nodes.

Everything is a node, so the type system is read from MDMNodes on demand and
cached for a short time (definitions change rarely, but they can change).
The values of Enums and the nodes a typed Link may point to are NOT cached as lists: a dropdown pages
through them in the database (page_values), and a typed name is looked up by name (lookup).
"""

from __future__ import annotations

import threading
import time

from .model import K, parse_ptype
from .repo import NodeRepo
from .uid import is_uid


class Catalog:
    TTL = 30.0

    def __init__(self, repo: NodeRepo, background: bool = False):
        """background=True (the app on SQL): when the TTL has passed, the catalog is refreshed in a background
        thread and the current one keeps answering — a request never waits for it. After invalidate() (a
        definition was changed) the next refresh() reads at once."""
        self.repo = repo
        self.background = background
        self._at = 0.0
        self._refreshing = False
        self._lock = threading.Lock()
        self.types: dict[str, dict] = {}
        self.types_by_name: dict[str, dict] = {}
        self.actions: dict[str, dict] = {}
        self.enums_by_name: dict[str, dict] = {}
        self.enum_uids: set[str] = set()
        self.permissions: list[dict] = []
        self._lookups: dict[tuple, list[dict]] = {}   # (base, arg, typed text) -> matching nodes, until the next reload

    def refresh(self, force: bool = False) -> "Catalog":
        if not force and time.time() - self._at < self.TTL:
            return self
        if not force and self.background and self._at > 0:
            with self._lock:
                if self._refreshing:
                    return self
                self._refreshing = True
            threading.Thread(target=self._reload_quietly, name="mdm-catalog", daemon=True).start()
            return self
        self._reload()
        return self

    def _reload_quietly(self) -> None:
        try:
            self._reload()
        except Exception:  # noqa: BLE001 - the current definitions stay; the next request tries again
            pass
        finally:
            self._refreshing = False

    def _reload(self) -> None:
        """The definitions in a few statements: NodeTypes, their Actions, the Enums (not their values) and the
        permission nodes."""
        types = self.repo.by_kind("NodeType")
        action_uids = sorted({a for t in types for a in t["properties"].get("actions", [])})
        actions = self.repo.get_many(action_uids)
        enums = self.repo.by_kind("Enum")
        perm_folder = self.repo.get(K.SYS_PERMISSIONS)
        permissions = self.repo.children(perm_folder, limit=1000) if perm_folder else []
        # swapped in together, so a request never sees half of the new definitions
        self.types = {t["uid"]: t for t in types}
        self.types_by_name = {t["name"].lower(): t for t in types}
        self.actions = actions
        self.enums_by_name = {e["name"].lower(): e for e in enums}
        self.enum_uids = {e["uid"] for e in enums}
        self.permissions = permissions
        self._lookups = {}
        self._at = time.time()

    def forget_values(self) -> None:
        """An Enum value (or a node a typed Link points to) changed: typed names are looked up again."""
        self._lookups = {}

    def invalidate(self) -> None:
        self._at = 0.0

    # -- types ---------------------------------------------------------------
    def effective_types(self, node: dict) -> list[dict]:
        """Node (implicit base) + the node's own types, without duplicates, in order."""
        out, seen = [], set()
        for uid in [K.NT_NODE] + list(node.get("types") or []):
            t = self.types.get(uid)
            if t and uid not in seen:
                seen.add(uid)
                out.append(t)
        return out

    def property_defs(self, node: dict) -> list[dict]:
        """Union of property definitions over the node's types. The same name with the same
        PropertyType is one property; `defined_by` lists every type that defines it."""
        merged: dict[tuple[str, str], dict] = {}
        for t in self.effective_types(node):
            for d in t["properties"].get("property_defs", []):
                key = (d["name"], d["ptype"])
                if key not in merged:
                    merged[key] = {**d, "defined_by": [t["name"]]}
                else:
                    merged[key]["defined_by"].append(t["name"])
                    merged[key]["priority"] = min(merged[key]["priority"], d["priority"])
        return sorted(merged.values(), key=lambda d: (d["priority"], d["group"]))

    def def_map(self, node: dict) -> dict[str, dict]:
        return {d["name"]: d for d in self.property_defs(node)}

    def node_actions(self, node: dict) -> list[dict]:
        out, seen = [], set()
        for t in self.effective_types(node):
            for a in t["properties"].get("actions", []):
                if a not in seen and a in self.actions:
                    seen.add(a)
                    out.append(self.actions[a])
        return out

    def type_uid(self, name_or_uid: str) -> str | None:
        if name_or_uid in self.types:
            return name_or_uid
        t = self.types_by_name.get(name_or_uid.lower())
        return t["uid"] if t else None

    def default_icon(self, types: list[str]) -> str:
        for uid in types:
            t = self.types.get(uid)
            if t and t["properties"].get("default_icon"):
                return t["properties"]["default_icon"]
        return "box"

    # -- the nodes a Link / Enum value may point to -------------------------
    def _target(self, base: str, arg: str | None) -> tuple[dict | None, list[str] | None] | None:
        """Where the values of a property live: (Enum node, None), (None, [type uid]) for a typed Link, or
        None when any node is allowed (Link(Node)). An unknown Enum / type has no values: (None, [])."""
        if base == "Enum":
            e = self.enums_by_name.get((arg or "").lower())
            return (e, None) if e else (None, [])
        if not arg or arg == "Node":
            return None
        if arg == "NodeType":
            return ("NodeType", None)   # the NodeTypes are in memory already
        tuid = self.type_uid(arg)
        return (None, [tuid] if tuid else [])

    def page_values(self, base: str, arg: str | None, q: str = "", offset: int = 0, limit: int = 40) -> list[dict] | None:
        """One page of the values a property may take, matching q (name or UID), from the database.
        None when any node is allowed (the caller searches all nodes)."""
        t = self._target(base, arg)
        if t is None:
            return None
        parent, types = t
        if parent == "NodeType":
            ql = (q or "").strip().lower()
            hits = [n for n in self.types.values() if not ql or ql in n["name"].lower() or ql == n["uid"].lower()]
            return sorted(hits, key=lambda n: n["name"].lower())[offset:offset + limit]
        if parent is None and not types:
            return []
        return self.repo.named(q, parent=parent, types=types, exact=False, offset=offset, limit=limit)

    def lookup(self, base: str, arg: str | None, text: str) -> list[dict] | None:
        """The values named `text` (or with that UID) — usually one; None when any node is allowed."""
        t = self._target(base, arg)
        if t is None:
            return None
        parent, types = t
        if parent == "NodeType":
            tl = text.strip().lower()
            return [n for n in self.types.values() if n["name"].lower() == tl or n["uid"].lower() == tl]
        if parent is None and not types:
            return []
        key = (base, (arg or "").lower(), text.strip().lower())
        if key not in self._lookups:
            self._lookups[key] = self.repo.named(text.strip(), parent=parent, types=types, exact=True, limit=3)
        return self._lookups[key]

    def enum_values(self, enum_name: str, limit: int = 1000) -> list[dict]:
        """All values of an Enum (small Enums only: survivorship, matching set-up)."""
        e = self.enums_by_name.get((enum_name or "").lower())
        return self.repo.children(e, limit=limit) if e else []

    def resolver(self):
        def resolve(base: str, arg: str, text: str):
            t = text.strip()
            hits = self.lookup(base if base == "Enum" else "Link", arg, t)
            if hits is None:  # any node
                if is_uid(t.upper()):
                    n = self.repo.get(t.upper())
                    return (n["uid"], None) if n else (None, f"'{t}' not found")
                hits = [n for n in self.repo.search(t, 5) if n["name"].lower() == t.lower()]
                if len(hits) == 1:
                    return hits[0]["uid"], None
                return None, (f"'{t}' is ambiguous — use the UID" if hits else f"'{t}' not found")
            by_uid = [n for n in hits if n["uid"] == t.upper()]
            if by_uid:
                return by_uid[0]["uid"], None
            hits = [n for n in hits if n["name"].lower() == t.lower()]
            if len(hits) == 1:
                return hits[0]["uid"], None
            return None, (f"'{t}' is ambiguous — use the UID" if hits else f"'{t}' not found")

        return resolve

    def namer(self, extra: dict[str, dict] | None = None):
        cache: dict[str, str] = {}

        def name_of(uid: str) -> str:
            if uid in cache:
                return cache[uid]
            n = (extra or {}).get(uid) or self.types.get(uid) or self.actions.get(uid)
            if n is None:
                n = self.repo.get(uid, include_deleted=True)
            cache[uid] = n["name"] if n else uid
            return cache[uid]

        return name_of


def ptype_base(ptype: str) -> str:
    return parse_ptype(ptype)[0]
