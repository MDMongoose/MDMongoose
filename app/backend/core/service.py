"""The service core: permissions, reads and edits of nodes that every feature builds on."""

from __future__ import annotations

import copy
import re
import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mdm.catalog import Catalog
from mdm.formats import format_value, join_list, parse_value
from mdm import links as lk
from mdm.hierarchy import RuleError, allowed_child_types, allowed_source, chain_of, check_child_types, check_depth
from mdm.model import NO_USER_DEFINED, READ_ONLY_PROPERTIES, K, clone, get_prop, new_node, parse_ptype, path_key, set_prop
from mdm.permissions import (
    ADMIN,
    GOVERNOR,
    GRANTABLE,
    LEVEL_BY_UID,
    LEVEL_NAMES,
    LEVEL_UIDS,
    MODIFIER,
    NONE,
    READER,
    User,
    effective_detail,
    level_for,
    level_of,
    perm_uids,
)
from mdm.repo import NodeRepo
from mdm.uid import is_reserved


class Forbidden(Exception):
    pass


class FieldError(RuleError):
    def __init__(self, field: str, msg: str):
        super().__init__(f"{field}: {msg}")
        self.field, self.msg = field, msg


class NotFound(Exception):
    pass


def as_text(value: Any) -> Any:
    """JSON value from the UI -> the text parse_value expects."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, list):
        return join_list(value)
    return value


class _Pool(ThreadPoolExecutor):
    """A thread pool whose tasks run in the caller's context (the request's statement timing, see sqlrepo.STATS)."""

    def submit(self, fn, /, *args, **kwargs):
        return super().submit(contextvars.copy_context().run, fn, *args, **kwargs)


_POOL = _Pool(max_workers=16, thread_name_prefix="mdm-read")
_LISTS = _Pool(max_workers=8, thread_name_prefix="mdm-list")
_OPEN = _Pool(max_workers=8, thread_name_prefix="mdm-open")   # the property page part of open()
MAX_BATCH_VIEWS = 80
_UID_RE = re.compile(r"^MDM-[0-9A-Z]+$")
PAGE = 40  # rows per page in the hierarchy and the Edit Children grid
DEF_KINDS = {"NodeType", "PropertyType", "Enum"}
ADMIN_GROUP = "admins"


def tile_cat(n: dict) -> str:
    """Colour family of a node's icon tile: def | source | reference | folder | entity."""
    p = n.get("properties") or {}
    if p.get("kind") in DEF_KINDS or p.get("handler") or n.get("parent_uid") in (K.SYS_ACTIONS, K.MODEL_ACTIONS,
                                                                                    K.SYS_PROPERTY_TYPES):
        return "def"
    ts = set(n.get("types") or [])
    if ts & {K.NT_TABLE, K.NT_RECORD}:
        return "source"
    if K.REFERENCE_DATA in (n.get("path") or []):
        return "reference"
    if ts <= {K.NT_FOLDER, K.NT_ORDERED, K.NT_PERMISSION}:
        return "folder"
    return "entity"


class ServiceCore:
    def __init__(self, repo: NodeRepo, catalog: Catalog | None = None, sources=None):
        self.repo = repo
        self.catalog = catalog or Catalog(repo)
        self.sources = sources  # SourceReader for Configure Load (catalogs, tables, preview rows)
        self.export_hook = None

    @property
    def catalog_name(self) -> str:
        table = getattr(self.repo, "table", "") or ""
        return table.split(".")[0] if table.count(".") == 2 else "workspace"

    def _sql(self):
        """The SQL executor of a warehouse-backed repository (None for the in-memory one)."""
        x = getattr(self.repo, "x", None)
        inner = getattr(x, "inner", None)
        return inner or x

    def _cat(self) -> Catalog:
        return self.catalog.refresh()

    def _node(self, uid: str) -> dict:
        n = self.repo.get(uid)
        if not n:
            raise NotFound(f"{uid} not found")
        return n

    def _chain(self, uid: str, key: str | None = None) -> list[dict]:
        """The node and its ancestors, root first: one statement when the UI sends the node's path_key."""
        ch = self.repo.chain(uid, key)
        if not ch:
            raise NotFound(f"{uid} not found")
        return ch

    def _definitions_changed(self, node: dict, children: bool = False) -> None:
        """After a delete: re-read the catalog when the node was a definition, an Enum value or a permission
        node (or, children=True, when its children were)."""
        cat = self.catalog
        cat.forget_values()   # names typed later are looked up again
        if (children and node["uid"] == K.SYS_PERMISSIONS) or (node["properties"] or {}).get("kind") \
                or node["parent_uid"] == K.SYS_PERMISSIONS:
            cat.invalidate()

    def _perm_nodes_all(self) -> list[dict]:
        """The permission nodes: loaded with the definitions (catalog), so a page view reads none."""
        return self._cat().permissions

    def _perm_nodes(self, uids) -> dict[str, dict]:
        """Permission nodes by uid. They all live in System / Permissions: that folder is read with one query
        that is the same for every request (so the app's cache answers it), not one lookup per chain."""
        uids = [u for u in dict.fromkeys(uids) if u]
        if not uids:
            return {}
        allp = {n["uid"]: n for n in self._perm_nodes_all()}
        missing = [u for u in uids if u not in allp]
        if missing:
            allp.update(self.repo.get_many(missing))
        return {u: allp[u] for u in uids if u in allp}

    def _level(self, user: User, chain: list[dict], perms: dict | None = None) -> int:
        if perms is None:
            perms = self._perm_nodes(perm_uids(chain))
        return level_for(user, chain, perms)

    def _need(self, user: User, chain: list[dict], level: int) -> None:
        got = self._level(user, chain)
        if got < level:
            raise Forbidden(f"you need {LEVEL_NAMES[level]} on '{chain[-1]['name']}' (you have {LEVEL_NAMES[got]})")

    def _action_level(self, action: str | dict) -> int:
        """Required Level of an action (node, uid or ui key); falls back to the default of its ui."""
        from mdm.seed import ACTION_LEVELS
        a = action if isinstance(action, dict) else (self.catalog.actions.get(action) or self.repo.get(action) or {})
        p = a.get("properties") or {}
        if p.get("Required Level"):
            return level_of(p["Required Level"])
        ui = p.get("ui") or (action if isinstance(action, str) else None)
        return ACTION_LEVELS.get(ui, MODIFIER)

    def _need_action(self, user: User, chain: list[dict], action_uid: str) -> int:
        """The user must reach the action's Required Level on the node. Returns the user's level."""
        self._cat()
        need = self._action_level(action_uid)
        got = self._level(user, chain)
        if got < need:
            a = self.catalog.actions.get(action_uid) or {"name": action_uid}
            raise Forbidden(f"{a['name']} needs {LEVEL_NAMES[need]} on '{chain[-1]['name']}' (you have {LEVEL_NAMES[got]})")
        return got

    def _check_governance(self, user: User, chain: list[dict], names, defs: dict[str, dict]) -> None:
        """Properties of the Governance group (Owner, Steward) can only be changed by a Governor."""
        gov = sorted(n for n in names if (defs.get(n) or {}).get("group") == "Governance")
        if gov:
            got = self._level(user, chain)
            if got < GOVERNOR:
                raise Forbidden(f"changing {', '.join(gov)} needs Governor on '{chain[-1]['name']}' "
                                f"(you have {LEVEL_NAMES[got]})")

    @staticmethod
    def _sync_kind(n: dict) -> None:
        """A node with the NodeType Enum is an enum list (its children are the values): keeps its `kind` marker in step."""
        p = n.setdefault("properties", {})
        if K.NT_ENUM in (n.get("types") or []):
            if not p.get("kind"):
                p["kind"] = "Enum"
        elif p.get("kind") == "Enum":
            p.pop("kind", None)

    def _capabilities(self, level: int) -> dict:
        lv = self._action_level
        return {"edit": level >= lv(K.A_EDIT), "lock": level >= lv(K.A_LOCK), "add": level >= lv(K.A_ADD_CHILD),
                "delete": level >= lv(K.A_DELETE), "move": level >= lv(K.A_CHANGE_PARENT),
                "edit_children": level >= lv(K.A_EDIT_CHILDREN), "govern": level >= GOVERNOR, "admin": level >= ADMIN}

    def _not_locked(self, node: dict) -> None:
        if node.get("locked"):
            raise RuleError(f"'{node['name']}' is locked — unlock it first")

    def _parse(self, ptype: str, value: Any, resolve) -> Any:
        r = parse_value(ptype, as_text(value), resolve)
        if not r.ok:
            raise RuleError(r.error)
        return r.value

    def _origins(self, nodes) -> dict[str, dict]:
        """The origins of the Child Link nodes among `nodes` (one read)."""
        ts = sorted({t for n in nodes if (t := lk.target_of(n))})
        return self.repo.get_many(ts) if ts else {}

    def _item(self, n: dict, counts: dict, level: int | None = None, origins: dict | None = None) -> dict:
        cat = self.catalog
        link = lk.is_link(n)
        o = (origins or {}).get(lk.target_of(n) or "") if link else None
        shown = o or n
        return {
            "uid": n["uid"], "k": path_key(n), "name": shown["name"] if o else (n["name"] if not link else f"{n['name']} (origin deleted)"),
            "icon": shown.get("icon") or cat.default_icon(shown["types"]),
            "types": shown["types"], "origin": n["origin"], "locked": n["locked"],
            "child_count": counts.get(n["uid"], 0),
            "is_link": link, "link_type": cat.namer()((n["properties"] or {}).get(lk.LINK_TYPE)) if link and (n["properties"] or {}).get(lk.LINK_TYPE) else ("link" if link else None),
            "target": lk.target_of(n), "level": LEVEL_NAMES.get(level) if level is not None else None,
            "cat": tile_cat(shown),
        }

    def _list(self, user: User, parent: dict, parent_chain: list[dict], perms: dict, offset: int = 0,
              limit: int = PAGE, q: str = "", pin: dict | None = None) -> dict:
        """One page of a node's children (Child Link nodes are children like any other)."""
        kids, total = self.repo.children_with_total(parent, offset=offset, limit=limit, q=q)
        if pin and pin["parent_uid"] == parent["uid"] and all(k["uid"] != pin["uid"] for k in kids):
            kids = kids + [pin]
        f_counts = _POOL.submit(self.repo.child_counts, [k["uid"] for k in kids], under=parent)
        origins = self._origins(kids)
        counts = f_counts.result()
        return self._page(user, parent_chain, perms, kids, total, counts, origins, offset, limit)

    def _page(self, user: User, parent_chain: list[dict], perms: dict, kids: list[dict], total: int, counts: dict,
              origins: dict, offset: int = 0, limit: int = PAGE) -> dict:
        perms = {**perms, **self._perm_nodes(sorted({p for k in kids for p in k.get("steward") or []} - set(perms)))}
        items = []
        for k in kids:
            lvl = level_for(user, parent_chain + [k], perms)
            if lvl >= READER:
                items.append(self._item(k, counts, level=lvl, origins=origins))
        return {"items": items, "total": total, "offset": offset, "next": min(offset + limit, total),
                "has_more": offset + limit < total}

    def tree(self, user: User, uid: str, key: str | None = None, chain: list[dict] | None = None,
             perms: dict | None = None) -> dict:
        """Three detailed levels (parent, current, children) plus the flat ancestor list. The reads go in two
        waves: both children pages (each with its total) at the same time, then one count query for every
        listed node and, for Child Links, one lookup of their origins."""
        self._cat()
        chain = chain or self._chain(uid, key)
        node = chain[-1]
        perms = perms if perms is not None else self._perm_nodes(perm_uids(chain))
        if level_for(user, chain, perms) < READER:
            raise Forbidden("no access")
        parent = chain[-2] if len(chain) > 1 else None
        f_sib = _LISTS.submit(self.repo.children_with_total, parent, 0, PAGE) if parent else None
        kids, ktotal = self.repo.children_with_total(node, 0, PAGE)
        sibs, stotal = f_sib.result() if parent else ([], 0)
        if parent and all(k["uid"] != node["uid"] for k in sibs):
            sibs = sibs + [node]   # the current node is always listed among its siblings
        listed = ([parent] if parent else []) + [node] + sibs + kids
        f_counts = _POOL.submit(self.repo.child_counts, list(dict.fromkeys(n["uid"] for n in listed)),
                                under=parent or node)
        origins = self._origins(listed)
        counts = f_counts.result()
        page = self._page(user, chain, perms, kids, ktotal, counts, origins)
        if parent:
            sib = self._page(user, chain[:-1], perms, sibs, stotal, counts, origins)
        else:
            sib = {"items": [self._item(node, counts, origins=origins)], "total": 1, "offset": 0, "next": 1, "has_more": False}
        return {
            "ancestors": [{"uid": a["uid"], "k": path_key(a), "name": a["name"],
                           "icon": a.get("icon") or self.catalog.default_icon(a["types"]),
                           "cat": tile_cat(a), "origin": a["origin"]} for a in chain[:-2]],
            "parent": self._item(parent, counts, level=level_for(user, chain[:-1], perms), origins=origins) if parent else None,
            "siblings": sib["items"], "siblings_page": {k: v for k, v in sib.items() if k != "items"},
            "current": node["uid"],
            "children": page["items"], "children_page": {k: v for k, v in page.items() if k != "items"},
            "current_item": self._item(node, counts, level=level_for(user, chain, perms), origins=origins),
        }

    def open(self, user: User, uid: str, key: str | None = None) -> dict:
        """One click: the hierarchy around the node and its property page in one request. The node, its
        ancestors and the permission nodes are read once; the two parts then read at the same time."""
        self._cat()
        chain = self._chain(uid, key)
        perms = self._perm_nodes(perm_uids(chain))
        f_node = _OPEN.submit(self.node_view, user, uid, chain=chain, perms=perms)
        tree = self.tree(user, uid, chain=chain, perms=perms)
        return {"tree": tree, "node": f_node.result()}

    def prefetch(self, user: User, levels: int = 5, per: int = PAGE, max_items: int = 4000) -> dict:
        """The first `per` children of every node in the top `levels` levels (root = level 1), for the UI cache at start."""
        self._cat()
        root = self._node(K.ROOT)
        kids, totals = self.repo.first_children(levels - 1, per)
        by_parent: dict[str, list[dict]] = {}
        for k in kids:
            by_parent.setdefault(k["parent_uid"], []).append(k)
        deep = [k["uid"] for k in kids if k["uid"] not in totals]
        counts = dict(totals)
        for i in range(0, len(deep), 5000):
            counts.update(self.repo.child_counts(deep[i:i + 5000]))
        origins = self._origins(kids)
        perm_ids = {p for k in [root] + kids for p in k.get("steward") or []}
        perms = self.repo.get_many(sorted(perm_ids)) if perm_ids else {}
        chains = {root["uid"]: [root]}
        root_level = level_for(user, [root], perms)
        if root_level < READER:
            raise Forbidden("no access")
        pages, items = {}, {root["uid"]: self._item(root, counts, level=root_level)}
        queue, n_items = [root], 0
        while queue and n_items < max_items:
            nxt = []
            for p in queue:
                chain = chains[p["uid"]]
                rows = []
                for k in by_parent.get(p["uid"], []):
                    lvl = level_for(user, chain + [k], perms)
                    if lvl < READER:
                        continue
                    it = self._item(k, counts, level=lvl, origins=origins)
                    rows.append(it)
                    items[k["uid"]] = it
                    chains[k["uid"]] = chain + [k]
                    nxt.append(k)
                total = totals.get(p["uid"], 0)
                pages[p["uid"]] = {"items": rows, "total": total, "offset": 0, "next": min(per, total),
                                   "has_more": per < total}
                n_items += len(rows)
            queue = [k for k in nxt if len(chains[k["uid"]]) <= levels and counts.get(k["uid"])]
        paths = {u: [c["uid"] for c in ch[:-1]] for u, ch in chains.items()}
        return {"levels": levels, "per": per, "pages": pages, "items": items, "paths": paths}

    def children_page(self, user: User, uid: str, offset: int = 0, limit: int = PAGE, q: str = "") -> dict:
        """The next page of a node's children for the hierarchy ("Load next 40", level filter)."""
        self._cat()
        chain = self._chain(uid)
        node = chain[-1]
        perms = self._perm_nodes(perm_uids(chain))
        if level_for(user, chain, perms) < READER:
            raise Forbidden("no access")
        return self._list(user, node, chain, perms, max(0, int(offset)), max(1, min(int(limit), 200)), q.strip())

    def node_view(self, user: User, uid: str, chain: list[dict] | None = None, perms: dict | None = None,
                  key: str | None = None) -> dict:
        cat = self._cat()
        chain = chain or self._chain(uid, key)
        node = chain[-1]
        level = self._level(user, chain, perms)
        if level < READER:
            raise Forbidden("no access")
        link = lk.is_link(node)
        defs = [d for d in cat.property_defs(node) if not link or d["name"] in lk.OWN_PROPERTIES]
        # every node the page names (Link / Enum / Array values): one lookup, before anything is displayed
        f_counts = _POOL.submit(self.repo.child_counts, [uid], under=node)
        wanted = set()
        for d in defs:
            base, arg = parse_ptype(d["ptype"])
            if base in ("Link", "Enum") or (base == "Array" and arg not in ("String", "OwnProperty")):
                v = get_prop(node, d["name"])
                wanted.update(x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and x)
        wanted -= set(cat.types) | set(cat.actions) | {c["uid"] for c in chain}
        refs = self.repo.get_many(sorted(wanted), include_deleted=True) if wanted else {}
        name_of = cat.namer({**refs, **{c["uid"]: c for c in chain}})
        rows = []
        for d in defs:
            base, arg = parse_ptype(d["ptype"])
            value = get_prop(node, d["name"])
            if d["name"] == "Allowed Child Type" and not value:
                src = allowed_source(chain[:-1])
                inherited = allowed_child_types(chain[:-1])
            else:
                src, inherited = None, []
            row = {
                "name": d["name"], "ptype": d["ptype"], "base": base, "arg": arg, "group": d["group"],
                "priority": d["priority"], "defined_by": d["defined_by"], "value": value,
                "display": format_value(d["ptype"], value, name_of),
                "read_only": d["name"] in READ_ONLY_PROPERTIES or base == "UID",
                "user_defined": bool((node.get("user_defined") or {}).get(d["name"])),
                "can_user_define": d["name"] not in NO_USER_DEFINED,
            }
            if inherited:
                row["inherited"] = {"display": join_list([name_of(u) for u in inherited]),
                                    "from": src["name"] if src else None}
            if base == "OwnProperty" or (base == "Array" and arg == "OwnProperty"):
                row["choices"] = self._own_properties(node)
            if base in ("Link", "Enum") and value:
                row["items"] = [value]          # node UIDs, resolved below in one lookup
            if base == "Array" and arg != "String":
                row["items"] = list(value or [])
            row["defined_by_uids"] = [cat.type_uid(t) for t in d["defined_by"]]
            rows.append(row)
        known = {**refs, **{c["uid"]: c for c in chain}, **cat.types, **cat.actions}
        for r in rows:
            if "items" in r:
                r["items"] = [self._ref(u, name_of, known) for u in r["items"]]
        self._enrich_rows(user, node, chain, rows, name_of)
        counts = f_counts.result()
        link_info = self._link_info(user, node, rows) if link else None
        return {
            "kind": (node["properties"] or {}).get("kind") or ("Action" if (node["properties"] or {}).get("handler") else None),
            "cat": tile_cat(node), "uid_num": node["uid_num"], "parent_uid": node["parent_uid"],
            "definition": self._definition(node, name_of),
            "link": link_info,
            "uid": node["uid"], "name": (link_info or {}).get("name") or node["name"],
            "icon": (link_info or {}).get("icon") or node.get("icon") or cat.default_icon(node["types"]),
            "types": [{"uid": t, "name": name_of(t)} for t in node["types"]],
            "icon_set": bool(node.get("icon")),
            "origin": node["origin"], "locked": node["locked"], "modified_at": node["modified_at"],
            "modified_by": node["modified_by"], "path": [{"uid": c["uid"], "name": c["name"]} for c in chain],
            "level": LEVEL_NAMES[level], "level_rank": level, "can": self._capabilities(level),
            "child_count": counts.get(uid, 0),
            "properties": rows, "actions": self._actions(user, node, chain, level, counts.get(uid, 0)),
            "allowed_child_types": [{"uid": a, "name": name_of(a)} for a in allowed_child_types(chain)],
        }

    def _link_info(self, user: User, node: dict, rows: list[dict]) -> dict:
        """A Child Link node: its rows are its own ones plus the origin's, read-only (changed on the origin)."""
        cat = self.catalog
        target = lk.target_of(node)
        o = self.repo.get(target) if target else None
        lt = (node["properties"] or {}).get(lk.LINK_TYPE)
        info = {"target": target, "link_type": cat.namer()(lt) if lt else "link", "deleted": o is None,
                "name": o["name"] if o else None, "icon": (o.get("icon") or cat.default_icon(o["types"])) if o else None,
                "path": [], "types": [], "readable": False}
        for row in rows:
            if row["name"] not in lk.EDITABLE_OWN:
                row["read_only"] = True
        if o is None:
            return info
        try:
            v = self.node_view(user, target)
        except Forbidden:
            return info
        info.update(path=v["path"], types=v["types"], readable=True, cat=v["cat"])
        own = {r["name"] for r in rows}
        for row in v["properties"]:
            if row["name"] in own:
                continue
            rows.append(dict(row, read_only=True, from_origin=True))
        return info

    def node_views(self, user: User, uids: list[str]) -> dict[str, dict]:
        """node_view of many nodes at once (the children of the opened node, for preloading)."""
        uids = list(dict.fromkeys(u for u in uids if u))[:MAX_BATCH_VIEWS]
        if not uids:
            return {}
        self._cat()
        memo = _MemoRepo(self.repo)
        nodes = memo.get_many(uids)
        by_parent: dict[str, list[str]] = {}
        for n in nodes.values():
            by_parent.setdefault(n["parent_uid"] or "", []).append(n["uid"])
        parents = memo.get_many([p for p in by_parent if p])
        for p, kids in by_parent.items():   # one child_counts query per parent (normally one)
            memo.child_counts(kids, under=parents.get(p))
        refs: set[str] = set()
        for n in nodes.values():
            refs.update(n.get("steward") or [])
            refs.update(n["types"])
            if lk.target_of(n):
                refs.add(lk.target_of(n))
            for v in (n["properties"] or {}).values():
                for x in (v if isinstance(v, list) else [v]):
                    if isinstance(x, str) and _UID_RE.match(x):
                        refs.add(x)
        memo.get_many(sorted(refs))         # Link / Enum / Array values, stewards, child links
        view = copy.copy(self)
        view.repo = memo
        out = {}
        for u in uids:
            if u not in nodes:
                continue
            try:
                out[u] = view.node_view(user, u)
            except (Forbidden, NotFound):
                continue
        return out

    def _actions(self, user: User, node: dict, chain: list[dict], level: int, n_children: int) -> list[dict]:
        out = []
        for a in self.catalog.node_actions(node):
            p = a["properties"]
            ui = p.get("ui")
            why = None
            need = self._action_level(a)
            if level < need:
                why = f"needs {LEVEL_NAMES[need]} — you have {LEVEL_NAMES[level]}"
            elif node["locked"] and ui not in ("lock", "export", "run_export", "run_matching", "review_matches",
                                               "record_matching", "golden_matching"):
                why = "node is locked"
            elif ui == "lock" and is_reserved(node["uid_num"]) and not user.is_admin:
                why = "seeded nodes can only be unlocked by admins"
            elif ui == "delete" and node["uid"] == K.ROOT:
                why = "the root cannot be deleted"
            elif ui == "change_parent" and node["uid"] == K.ROOT:
                why = "the root has no parent"
            elif ui in ("add_child", "edit_children", "add_child_link"):
                try:
                    check_depth(chain, 1)
                except RuleError as e:
                    why = str(e)
            elif ui == "delete_all_children" and n_children == 0:
                why = "no children"
            elif ui == "refresh_golden" and K.NT_MASTER_DATA in node["types"] \
                    and not node["properties"].get("Survivorship Rule"):
                why = "set a Survivorship Rule first"
            elif ui in ("run_matching", "review_matches") and K.NT_MASTER_DATA in node["types"] \
                    and not node["properties"].get("Match Rule"):
                why = "set a Match Rule first"
            elif ui == "golden_matching" and node.get("origin") != "generated":
                why = "only on golden records"
            elif ui == "load_children" and not node["properties"].get("Table Name"):
                why = "set Table Name first (Configure Load)"
            out.append({"uid": a["uid"], "name": a["name"], "group": p.get("action_group"), "ui": ui,
                        "required_level": LEVEL_NAMES[need],
                         "mode": p.get("mode"), "handler": p.get("handler"), "enabled": why is None, "why": why})
        order = {"Self": 0, "Children": 1, "Load": 2, "Matching": 3, "Survivorship": 4, "Export": 5}
        return sorted(out, key=lambda a: order.get(a["group"], 9))

    def search(self, user: User, text: str, kind: str = "", limit: int = 30, under: str = "") -> list[dict]:
        """kind: '' any | manual | loaded | generated | <NodeType uid>. under: only descendants of that node."""
        cat = self._cat()
        scope = self._node(under) if under and under != K.ROOT else None
        hits = self.repo.search(text, 200 if kind else limit, under=scope)
        out = []
        name_of = cat.namer()
        for h in hits:
            if kind in ("manual", "loaded", "generated") and h["origin"] != kind:
                continue
            if kind.startswith("MDM-") and kind not in h["types"]:
                continue
            chain = chain_of(self.repo, h)
            if self._level(user, chain) >= READER:
                out.append({"uid": h["uid"], "name": h["name"], "icon": h.get("icon") or cat.default_icon(h["types"]),
                            "path": " / ".join(c["name"] for c in chain[:-1]), "path_uids": [c["uid"] for c in chain[:-1]],
                            "origin": h["origin"],
                            "types": [name_of(t) for t in h["types"]], "cat": tile_cat(h), "_chain": chain})
            if len(out) >= limit:
                break
        nodes = {c["uid"]: c for o in out for c in o["_chain"]}
        counts = self.repo.child_counts(list(nodes)) if nodes else {}
        brief = {u: {"uid": u, "name": n["name"], "icon": n.get("icon") or cat.default_icon(n["types"]), "origin": n["origin"],
                     "cat": tile_cat(n), "types": [name_of(t) for t in n["types"]], "locked": n.get("locked", False),
                     "child_count": counts.get(u, 0) + len(n.get("child_links") or [])} for u, n in nodes.items()}
        for o in out:
            chain = o.pop("_chain")
            o["path_nodes"] = [brief[c["uid"]] for c in chain[:-1]]
            o["child_count"] = brief[o["uid"]]["child_count"]
        return out

    def options(self, user: User, ptype: str, q: str = "", offset: int = 0, limit: int = 200) -> list[dict]:
        """Candidates for a Link/Enum/Array picker; one page (offset, limit) of the matches of q (name or UID)."""
        cat = self._cat()
        base, arg = parse_ptype(ptype)
        if base == "Array" and arg in ("String", "OwnProperty"):
            return []
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 500))
        page = cat.page_values("Enum" if base == "Enum" else "Link", arg, q, offset, limit)
        if page is None:   # any node: the general search
            page = (self.repo.search(q, offset + limit) if q else [])[offset:offset + limit]
        return [{"uid": c["uid"], "name": c["name"]} for c in page]

    def _own_properties(self, node: dict) -> list[str]:
        """OwnProperty values: Name and the properties of the node's rows (its allowed child types, or the
        types of its children when it has no own constraint)."""
        cat = self._cat()
        types = list(node.get("allowed_child_types") or [])
        if not types:
            for c in self.repo.children(node["uid"], limit=50):
                types += [t for t in c["types"] if t not in types]
        names = ["Name"]
        for t in types:
            for d in ((cat.types.get(t) or {}).get("properties") or {}).get("property_defs", []):
                if d["name"] not in names:
                    names.append(d["name"])
        return names

    def _apply(self, node: dict, changes: dict[str, Any], defs: dict[str, dict], resolve, mark_user_defined: bool,
               user_defined: dict[str, bool] | None = None) -> dict:
        n = clone(node)
        for name, raw in changes.items():
            d = defs.get(name)
            if d is None:
                raise RuleError(f"'{name}' is not a property of '{node['name']}'")
            if name in READ_ONLY_PROPERTIES or parse_ptype(d["ptype"])[0] == "UID":
                raise RuleError(f"'{name}' is read-only")
            try:
                value = self._parse(d["ptype"], raw, resolve)
            except RuleError as e:
                raise FieldError(name, str(e)) from None
            base, arg = parse_ptype(d["ptype"])
            if value and (base == "OwnProperty" or (base == "Array" and arg == "OwnProperty")):
                own = self._own_properties(n)
                bad = [v for v in (value if isinstance(value, list) else [value]) if v not in own]
                if bad:
                    raise FieldError(name, f"{', '.join(bad)}: not a property of the rows — use {', '.join(own)}")
            if name == "Name" and not value:
                raise RuleError("Name cannot be empty")
            if name == "Types" and not value:
                raise RuleError("a node needs at least one type")
            if name == "Max Child Depth" and value is not None and value < 0:
                raise RuleError("Max Child Depth must be 0 or more")
            set_prop(n, name, value)
            if mark_user_defined and node["origin"] != "manual" and name not in NO_USER_DEFINED:
                n.setdefault("user_defined", {})[name] = True
        for name, flag in (user_defined or {}).items():
            if name in NO_USER_DEFINED:
                continue
            ud = n.setdefault("user_defined", {})
            if flag:
                ud[name] = True
            else:
                ud.pop(name, None)
        return n

    def edit(self, user: User, uid: str, changes: dict[str, Any], expected: str | None = None,
             user_defined: dict[str, bool] | None = None) -> dict:
        cat = self._cat()
        node = self._node(uid)
        chain = chain_of(self.repo, node)
        self._need_action(user, chain, K.A_EDIT)
        self._not_locked(node)
        if lk.is_link(node):
            other = sorted(set(changes) - lk.EDITABLE_OWN)
            if other:
                raise RuleError(f"{', '.join(other)}: a Child Link shows the values of its origin — change them on the origin node")
        self._check_governance(user, chain, set(changes) | set(user_defined or {}), cat.def_map(node))
        if "Types" in changes:  # the new types decide which properties exist
            tmp = self._apply(node, {"Types": changes["Types"]}, cat.def_map(node), cat.resolver(), False)
            check_child_types(chain[:-1], tmp["types"], cat.namer())
            defs = {**cat.def_map(node), **cat.def_map(tmp)}
        else:
            defs = cat.def_map(node)
        n = self._apply(node, changes, defs, cat.resolver(), True, user_defined)
        self._sync_kind(n)
        if K.NT_EXPORT in n["types"]:
            self._prepare_export(user, n, node)
        if "Max Child Depth" in changes and n["max_child_depth"] is not None:
            h = self.repo.subtree_stats(n)["height"]
            if h > n["max_child_depth"]:
                raise RuleError(f"the node already has {h} level(s) of children")
        self.repo.upsert([n], user.name, {uid: expected} if expected else None)
        if (n["properties"] or {}).get("kind") or (node["properties"] or {}).get("kind") or n["parent_uid"] == K.SYS_PERMISSIONS:
            cat.invalidate()   # definitions and permission nodes are read with the catalog
        if "Name" in changes:
            cat.forget_values()
        if K.NT_EXPORT in n["types"]:
            self._after_export_saved(user, n, node)
        return self.node_view(user, uid)

    def toggle_lock(self, user: User, uid: str) -> dict:
        node = self._node(uid)
        self._need_action(user, chain_of(self.repo, node), K.A_LOCK)
        if node["locked"] and is_reserved(node["uid_num"]) and not user.is_admin:
            raise Forbidden("seeded nodes can only be unlocked by admins")
        self.repo.upsert([dict(node, locked=not node["locked"])], user.name)
        return {"locked": not node["locked"]}

    def change_parent(self, user: User, uid: str, new_parent_uid: str) -> dict:
        cat = self._cat()
        node, target = self._node(uid), self._node(new_parent_uid)
        if node["uid"] == K.ROOT:
            raise RuleError("the root has no parent")
        if target["uid"] == node["uid"] or node["uid"] in target["path"]:
            raise RuleError("cannot move a node under itself")
        self._need_action(user, chain_of(self.repo, node), K.A_CHANGE_PARENT)
        self._not_locked(node)
        tchain = chain_of(self.repo, target)
        self._need_action(user, tchain, K.A_ADD_CHILD)  # the new parent gets a child
        self._not_locked(target)
        check_child_types(tchain, node["types"], cat.namer())
        check_depth(tchain, 1 + self.repo.subtree_stats(node)["height"])
        return {"moved": self.repo.move_subtree(node, target, user.name)}

    def check_action(self, user: User, uid: str, action_uid: str) -> tuple[dict, dict]:
        """Validate that user may run a job/pipeline action on uid. Returns (node, action)."""
        self._cat()
        node = self._node(uid)
        chain = chain_of(self.repo, node)
        level = self._level(user, chain)
        acts = {a["uid"]: a for a in self._actions(user, node, chain, level,
                                                    self.repo.child_counts([uid], under=node)[uid])}
        a = acts.get(action_uid)
        if not a:
            raise RuleError("this action is not available on this node")
        if not a["enabled"]:
            raise Forbidden(a["why"])
        return node, a

    def _ref(self, uid: str, name_of, known: dict | None = None) -> dict:
        n = (known.get(uid) if known is not None else self.repo.get(uid, include_deleted=True)) if uid else None
        if not n:
            return {"uid": uid, "name": name_of(uid), "icon": "box", "cat": "entity"}
        return {"uid": uid, "name": n["name"], "icon": n.get("icon") or self.catalog.default_icon(n["types"]),
                "cat": tile_cat(n)}

    def _enrich_rows(self, user: User, node: dict, chain: list[dict], rows: list[dict], name_of) -> None:
        names = [r["name"] for r in rows]
        for r in rows:
            r["conflict"] = names.count(r["name"]) > 1
            if r["name"] == "Allowed Child Type":
                eff = allowed_child_types(chain)
                src = allowed_source(chain)
                nm = ", ".join(name_of(t) for t in eff)
                if not eff:
                    r["note"] = "Effective: no constraint (nothing set here or above)"
                elif src and src["uid"] == node["uid"]:
                    above = allowed_source(chain[:-1])
                    r["note"] = (f"Set here: children and all lower levels may only be {nm}"
                                 + (f" — replaces the list inherited from {above['name']}" if above else "")
                                 + ". A lower node can set its own list.")
                else:
                    r["note"] = (f"Inherited from {src['name']}: children may only be {nm}. "
                                 "Set a list here to replace it for this branch.")
            elif r["name"] == "Owner" and not r["value"]:
                owner_node = next((c for c in reversed(chain[:-1]) if c.get("owner")), None)
                if owner_node:
                    r["display"] = owner_node["owner"]
                    r["note"] = f"inherited from {owner_node['name']}"
            elif r["name"] == "Max Child Depth":
                r["display"] = ("∞ unlimited" if r["value"] is None else
                                "0 — no children" if r["value"] == 0 else str(r["value"]))
            elif r["name"] == "Icon":
                r["display"] = node.get("icon") or self.catalog.default_icon(node["types"])
                r["note"] = "(set on node)" if node.get("icon") else "(default of first type)"
            elif r["name"] == "Steward":
                r["perms"] = self.effective_permissions(node, chain)
                r["empty_text"] = "no own entries — inherits"
                r.update(self._steward_editor(node, chain))

    def _steward_editor(self, node: dict, chain: list[dict]) -> dict:
        """What the Steward editor needs: this node's own entries, what is granted above it, and the known UC groups."""
        perm_nodes = self._perm_nodes(perm_uids(chain))
        own = []
        for u in node.get("steward") or []:
            pn = perm_nodes.get(u)
            if pn:
                own.append({"uid": u, "group": (pn["properties"] or {}).get("Group") or "",
                            "level": LEVEL_NAMES[LEVEL_BY_UID.get((pn["properties"] or {}).get("Level"), NONE)]})
        above_chain = chain[:-1]
        granted = any(n.get("steward") for n in above_chain)
        above = [{"group": g, "level": LEVEL_NAMES[d["level"]], "from": d["from"]["name"]}
                 for g, d in sorted(effective_detail(above_chain, perm_nodes).items()) if d["level"] > NONE]
        known = sorted({(p["properties"] or {}).get("Group") for p in self._perm_nodes_all()}
                       - {None, "", ADMIN_GROUP})
        return {"own": own, "above": above, "grants_here": not granted, "known_groups": known}

    def resolve_permissions(self, user: User, entries: list[dict]) -> list[str]:
        """[{group, level}] → Permission node uids, in order."""
        want = []
        for e in entries:
            g = str(e.get("group") or "").strip()
            lvl = level_of(str(e.get("level") or "Reader"))
            if not g:
                raise RuleError("a UC group name is required")
            if lvl not in GRANTABLE:
                raise RuleError(f"level must be one of {', '.join(LEVEL_NAMES[x] for x in GRANTABLE)}")
            want.append((g, lvl))
        existing = {}
        for p in self.repo.children(K.SYS_PERMISSIONS, limit=1000):
            pp = p["properties"] or {}
            existing.setdefault((pp.get("Group"), pp.get("Level")), p["uid"])
        missing = sorted({(g, l) for g, l in want if (g, LEVEL_UIDS[l]) not in existing})
        if missing:
            if not user.is_admin:
                raise Forbidden("only admins can add a new UC group permission: "
                                + ", ".join(f"{g} · {LEVEL_NAMES[l]}" for g, l in missing))
            folder = self._node(K.SYS_PERMISSIONS)
            nums = self.repo.allocate(len(missing))
            created = [new_node(n, f"{g} · {LEVEL_NAMES[l]}", [K.NT_PERMISSION], folder, locked=True, max_child_depth=0,
                                properties={"Group": g, "Level": LEVEL_UIDS[l]}) for n, (g, l) in zip(nums, missing)]
            self.repo.upsert(created, user.name)
            for c in created:
                existing[(c["properties"]["Group"], c["properties"]["Level"])] = c["uid"]
            self.catalog.invalidate()   # the permission nodes are part of the definitions
        return list(dict.fromkeys(existing[(g, LEVEL_UIDS[l])] for g, l in want))

    def effective_permissions(self, node: dict, chain: list[dict]) -> list[dict]:
        groups = sorted({(p["properties"] or {}).get("Group") for p in self._perm_nodes_all()} - {None})
        perm_nodes = self._perm_nodes(perm_uids(chain))
        detail = effective_detail(chain, perm_nodes)
        out = [{"group": ADMIN_GROUP, "level": "Admin", "source": "global — all privileges everywhere", "warn": ""}]
        for g in groups:
            if g == ADMIN_GROUP:
                continue
            d = detail.get(g)
            if not d:
                out.append({"group": g, "level": "None", "source": "not granted", "warn": ""})
                continue
            src = "set here" if d["from"]["uid"] == node["uid"] else f"inherited from {d['from']['name']}"
            out.append({"group": g, "level": LEVEL_NAMES[d["level"]], "source": src, "warn": d["note"]})
        return out

    def _definition(self, node: dict, name_of) -> dict | None:
        p = node["properties"] or {}
        if p.get("kind") == "NodeType":
            used_by = len(self.repo.by_types([node["uid"]], limit=10_000))
            return {"kind": "NodeType", "default_icon": p.get("default_icon") or "box",
                    "property_defs": [{**d, "base": parse_ptype(d["ptype"])[0], "param": parse_ptype(d["ptype"])[1] or ""}
                                      for d in p.get("property_defs", [])],
                    "actions": [self._action_ref(a, name_of) for a in p.get("actions", [])],
                    "implemented_by": used_by,
                    "editable": not node["locked"]}
        if p.get("kind") == "PropertyType":
            return {"kind": "PropertyType", "storage": p.get("storage"), "editor": p.get("editor")}
        if p.get("handler"):
            assigned = [t for t in self.catalog.types.values() if node["uid"] in t["properties"].get("actions", [])]
            return {"kind": "Action", "handler": p.get("handler"), "params": p.get("params"),
                    "required_level": LEVEL_NAMES[self._action_level(node)],
                    "action_group": p.get("action_group"), "mode": p.get("mode"), "ui": p.get("ui"),
                    "code": _handler_source(p.get("handler")),
                    "assigned_to": [{"uid": t["uid"], "name": t["name"]} for t in assigned]}
        if K.NT_EXPORT in node["types"]:
            from mdm import exports
            spec = exports.spec_of(node, name_of)
            return {"kind": "Export", "export_type": exports.KIND_LABEL.get(spec["kind"] or "", "—"),
                    "location": exports.location(spec, self.catalog_name) if spec["kind"] else None,
                    "sql": self.export_sql(node)}
        if p.get("kind") == "Enum":
            return {"kind": "Enum", "values": [{"uid": v["uid"], "name": v["name"]} for v in self.repo.children(node, limit=500)]}
        return None

    def _action_ref(self, uid: str, name_of) -> dict:
        a = self.catalog.actions.get(uid) or self.repo.get(uid) or {"name": name_of(uid), "properties": {}}
        return {"uid": uid, "name": a["name"], "handler": (a.get("properties") or {}).get("handler"),
                "group": (a.get("properties") or {}).get("action_group")}

    def meta(self, user: User) -> dict:
        self._cat()
        groups = sorted({(p["properties"] or {}).get("Group") for p in self._perm_nodes_all()} - {None})
        return {"groups": [g for g in groups if g != ADMIN_GROUP],
                "node_types": [{"uid": t["uid"], "name": t["name"], "icon": t["properties"].get("default_icon")}
                               for t in self.catalog.types.values()],
                "property_types": ["UID", "Number", "Float", "String", "Timestamp", "Link", "Enum", "Array",
                                   "Expression", "Icon", "Boolean", "OwnProperty"],
                "actions": [{"uid": a["uid"], "name": a["name"], "handler": a["properties"].get("handler"),
                             "group": a["properties"].get("action_group")}
                            for a in self.repo.children(K.SYS_ACTIONS) + self.repo.children(K.MODEL_ACTIONS)],
                "model_node_types": K.MODEL_NODE_TYPES, "model_actions": K.MODEL_ACTIONS}

    def valid_parents(self, user: User, uid: str, q: str = "", offset: int = 0, limit: int = 100) -> list[dict]:
        """Nodes the node may be moved under: their Allowed Child Type accepts its types (or they have none),
        the depth fits and the user may add children there. The current parent comes first. One page of them."""
        cat = self._cat()
        node = self._node(uid)
        height = self.repo.subtree_stats(node)["height"]
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 200))
        if q:
            cands = self.repo.search(q, min(2000, 3 * (offset + limit) + 40))
        else:
            chain = chain_of(self.repo, node)
            cands = [c for c in chain[:-1]]
            for c in chain[-4:-1]:
                cands += self.repo.children(c, limit=offset + limit + PAGE)
        out, seen = [], set()
        for c in cands:
            current = c["uid"] == node["parent_uid"]
            if c["uid"] == uid or uid in c["path"] or c["uid"] in seen or (c["locked"] and not current):
                continue
            seen.add(c["uid"])
            if (c["properties"] or {}).get("kind") in ("NodeType", "PropertyType"):
                continue
            ch = chain_of(self.repo, c)
            if current:  # where the node is now: always offered, it is the default
                out.append({"uid": c["uid"], "name": c["name"], "path": " / ".join(x["name"] for x in ch[:-1]),
                            "current": True, "icon": c.get("icon") or cat.default_icon(c["types"]), "cat": tile_cat(c)})
                continue
            try:
                check_child_types(ch, node["types"])
                check_depth(ch, 1 + height)
            except RuleError:
                continue
            if self._level(user, ch) < self._action_level(K.A_ADD_CHILD):
                continue
            out.append({"uid": c["uid"], "name": c["name"], "path": " / ".join(x["name"] for x in ch[:-1]),
                        "current": c["uid"] == node["parent_uid"], "icon": c.get("icon") or cat.default_icon(c["types"]),
                        "cat": tile_cat(c)})
        out.sort(key=lambda o: not o["current"])
        return out[offset:offset + limit]

    def _check_defs(self, defs: list[dict]) -> list[dict]:
        cat = self._cat()
        out, names = [], set()
        for d in defs:
            name = (d.get("name") or "").strip()
            if not name:
                continue
            if name in names:
                raise RuleError(f"property '{name}' is defined twice")
            names.add(name)
            base = (d.get("base") or parse_ptype(d.get("ptype", "String"))[0]).strip()
            param = (d.get("param") if "param" in d else parse_ptype(d.get("ptype", ""))[1]) or ""
            if base not in ("UID", "Number", "Float", "String", "Timestamp", "Link", "Enum", "Array",
                            "Expression", "Icon", "Boolean", "OwnProperty"):
                raise RuleError(f"unknown PropertyType {base}")
            if base in ("Link", "Enum", "Array") and not param:
                raise RuleError(f"'{name}': {base} needs a parameter")
            if base == "Enum" and param.lower() not in cat.enums_by_name:
                raise RuleError(f"'{name}': there is no Enum named {param}")
            if base in ("Link", "Array") and param not in ("Node", "NodeType", "String", "OwnProperty") \
                    and not cat.type_uid(param):
                raise RuleError(f"'{name}': there is no NodeType named {param}")
            ptype = f"{base}({param})" if param and base in ("Link", "Enum", "Array", "Expression") else base
            out.append({"name": name, "ptype": ptype, "group": (d.get("group") or "General").strip(),
                        "priority": int(d.get("priority") if d.get("priority") not in (None, "") else 10)})
        return out

    def save_node_type(self, user: User, uid: str, defs: list[dict], actions: list[str] | None = None,
                       default_icon: str | None = None, expected: str | None = None) -> dict:
        node = self._node(uid)
        if (node["properties"] or {}).get("kind") != "NodeType":
            raise RuleError(f"'{node['name']}' is not a NodeType")
        chain = chain_of(self.repo, node)
        self._need_action(user, chain, K.A_EDIT)
        self._not_locked(node)
        n = clone(node)
        n["properties"]["property_defs"] = self._check_defs(defs)
        if actions is not None:
            n["properties"]["actions"] = [a for a in actions if a]
        if default_icon:
            n["properties"]["default_icon"] = default_icon
        self.repo.upsert([n], user.name, {uid: expected} if expected else None)
        self.catalog.invalidate()
        return self.node_view(user, uid)

    def create_action(self, user: User, name: str, handler: str, group: str = "Self", mode: str = "job",
                      required_level: str | None = None) -> dict:
        folder = self._node(K.MODEL_ACTIONS)
        self._need_action(user, chain_of(self.repo, folder), K.A_ADD_CHILD)
        handler = (handler or "").strip()
        if not handler.startswith("mdm_custom."):
            raise RuleError("custom action handlers must live in the mdm_custom package, e.g. mdm_custom.actions.validate_vat")
        num = self.repo.allocate(1)[0]
        lvl = level_of(required_level) if required_level else MODIFIER
        if lvl not in GRANTABLE:
            raise RuleError("Required Level must be Reader, Modifier, Supervisor, Manager or Governor")
        n = new_node(num, name.strip(), [K.NT_FOLDER, K.NT_ACTION], folder, icon="bolt", max_child_depth=0,
                     properties={"handler": handler, "params": "UID", "action_group": group, "ui": "custom",
                                 "mode": mode, "Required Level": LEVEL_UIDS[lvl]})
        self.repo.upsert([n], user.name)
        self.catalog.invalidate()
        return {"uid": n["uid"]}



def _handler_source(handler: str | None) -> str | None:
    """Source code of a python handler (mdm.* or mdm_custom.*), for the Action definition view."""
    import importlib
    import inspect
    if not handler or ":" in handler:
        return None
    mod, _, fn = handler.rpartition(".")
    if not (mod.startswith("mdm.") or mod.startswith("mdm_custom")):
        return None
    try:
        obj = getattr(importlib.import_module(mod), fn)
        src = inspect.getsource(obj)
        lines = src.splitlines()
        return "\n".join(lines[:40]) + ("\n    ..." if len(lines) > 40 else "")
    except Exception:  # noqa: BLE001
        return None


def _custom_functions() -> list[dict]:
    import importlib
    import inspect
    out = []
    try:
        mod = importlib.import_module("mdm_custom.survivorship")
    except Exception:  # noqa: BLE001
        return out
    for name, fn in inspect.getmembers(mod, inspect.isfunction):
        if fn.__module__ == mod.__name__ and not name.startswith("_"):
            out.append({"path": f"mdm_custom.survivorship.{name}", "doc": (fn.__doc__ or "").strip().split("\n")[0],
                        "code": inspect.getsource(fn)})
    return out


class _MemoRepo:
    """Request-scoped read memo in front of a repo, for node_views: repeated reads of the same nodes."""

    def __init__(self, repo):
        self._r = repo
        self._nodes: dict[str, dict | None] = {}
        self._anc: dict[tuple, dict] = {}
        self._counts: dict[str, int] = {}
        self._kids: dict[tuple, list] = {}
        self._any: dict[str, dict | None] = {}

    def __getattr__(self, name):  # anything not memoised (search, by_types, ...) goes straight through
        if name in ("upsert", "insert_missing", "allocate", "soft_delete_subtree", "move_subtree",
                    "lift_children", "hard_delete"):
            raise AttributeError(f"{name} is not available while building views")
        return getattr(self._r, name)

    def chain(self, uid, key=None):
        n = self.get(uid)
        if not n:
            return None
        anc = self.ancestors(n)
        return [anc[u] for u in n["path"] if u in anc] + [n]

    def get_many(self, uids, include_deleted=False):
        if include_deleted:  # references may point at deleted nodes: memo them separately
            want = list(dict.fromkeys(uids))
            missing = [u for u in want if self._nodes.get(u) is None and u not in self._any]
            if missing:
                got = self._r.get_many(missing, include_deleted=True)
                for u in missing:
                    self._any[u] = got.get(u)
            return {u: clone(self._nodes.get(u) or self._any.get(u)) for u in want if self._nodes.get(u) or self._any.get(u)}
        missing = [u for u in dict.fromkeys(uids) if u not in self._nodes]
        if missing:
            got = self._r.get_many(missing)
            for u in missing:
                self._nodes[u] = got.get(u)
        return {u: clone(self._nodes[u]) for u in uids if self._nodes.get(u)}

    def get(self, uid, include_deleted=False):
        if include_deleted:  # references (_ref) may point at deleted nodes: memo them separately
            if self._nodes.get(uid) is not None:
                return clone(self._nodes[uid])
            if uid not in self._any:
                self._any[uid] = self._r.get(uid, include_deleted=True)
            return clone(self._any[uid]) if self._any[uid] else None
        return self.get_many([uid]).get(uid)

    def ancestors(self, node):
        key = tuple(node.get("path") or [])
        if key not in self._anc:
            self._anc[key] = self._r.ancestors(node)
            for u, n in self._anc[key].items():
                self._nodes.setdefault(u, n)
        return {u: clone(n) for u, n in self._anc[key].items()}

    def child_counts(self, uids, under=None):
        missing = [u for u in uids if u not in self._counts]
        if missing:
            got = self._r.child_counts(missing, under=under)
            for u in missing:
                self._counts[u] = got.get(u, 0)
        return {u: self._counts[u] for u in uids if self._counts.get(u)}

    def children(self, parent, offset=0, limit=None, q="", sort=None, filters=None):
        if sort or filters:
            return self._r.children(parent, offset=offset, limit=limit, q=q, sort=sort, filters=filters)
        key = (parent if isinstance(parent, str) else parent["uid"], offset, limit, q)
        if key not in self._kids:
            self._kids[key] = self._r.children(parent, offset=offset, limit=limit, q=q)
        return [clone(n) for n in self._kids[key]]
