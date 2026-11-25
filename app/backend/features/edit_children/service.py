"""Edit Children grid."""

from __future__ import annotations

from mdm import links as lk
from mdm.flatten import SKIP_IN_GRID, flatten
from mdm.hierarchy import RuleError, allowed_child_types, chain_of, check_child_types, check_depth
from mdm.model import READ_ONLY_PROPERTIES, K, new_node, parse_ptype
from mdm.permissions import GOVERNOR, LEVEL_NAMES, READER, User

from ...core.service import (
    PAGE,
    Forbidden,
)


class EditChildrenService:
    def children_table(self, user: User, uid: str, levels: int = 1, offset: int = 0, limit: int = PAGE,
                       q: str = "", sort: dict | None = None, filters: list[dict] | None = None) -> dict:
        """One page of the Edit Children grid."""
        cat = self._cat()
        node = self._node(uid)
        chain = chain_of(self.repo, node)
        level = self._level(user, chain)
        if level < READER:
            raise Forbidden("no access")
        levels = max(1, min(int(levels), 3))  # 1 = children only ("off"), 2-3 = exploded
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 500))
        kids = self.repo.children(node, offset=offset, limit=limit, q=q, sort=sort, filters=filters)
        total = self.repo.count_children(node, q=q, filters=filters)
        all_types = [v for v, _ in self.repo.distinct_values(node, {"name": "Types", "ptype": "Array(NodeType)"}, limit=200) if v]
        allowed = allowed_child_types(chain)
        # no children yet: the types a new child gets give the columns, so rows can be added in bulk
        col_types = all_types or list(allowed) or [K.NT_FOLDER]
        cols, rows = flatten(self.repo, cat, kids, max(0, levels - 1), False, SKIP_IN_GRID, roots_for_tree=[node],
                             preload=False, per_parent=PAGE, extra_types=[[t] for t in col_types])
        for c in cols:
            c["level"] += 1
            c["key"] = f"L{c['level']}." + c["key"].split(".", 1)[1]
            base, arg = parse_ptype(c["ptype"])
            c["base"], c["arg"] = base, arg
            c["read_only"] = c["name"] in READ_ONLY_PROPERTIES or base == "UID"
            c["server_filter"] = c["level"] == 1   # Enum values: the cell editor searches them; checked on save
        shift = lambda k: f"L{int(k[1:].split('.', 1)[0]) + 1}." + k.split(".", 1)[1]
        for r in rows:
            r["values"] = {shift(k): v for k, v in r["values"].items()}
            r["undefined"] = [shift(k) for k in r["undefined"]]
        name_of = cat.namer()
        level_types: dict[int, list[str]] = {1: [name_of(t) for t in col_types]}
        seen_nodes = {k["uid"]: k for k in kids}
        missing = {u for r in rows for u in r["uids"][1:] if u and u not in seen_nodes}
        seen_nodes.update(self.repo.get_many(sorted(missing)))
        for r in rows:
            for lvl, u in enumerate(r["uids"]):
                if lvl and u and u in seen_nodes:
                    for t in seen_nodes[u]["types"]:
                        nm = name_of(t)
                        if nm not in level_types.setdefault(lvl + 1, []):
                            level_types[lvl + 1].append(nm)
        acts = {a["properties"].get("ui"): a for a in cat.node_actions(node)}
        return {"uid": uid, "name": node["name"], "levels": levels, "columns": cols, "rows": rows,
                "total": total, "offset": offset, "next": min(offset + limit, total), "has_more": offset + limit < total,
                "types": [name_of(t) for t in node["types"]],
                "handler": (acts.get("edit_children") or {}).get("properties", {}).get("handler", "mdm.actions.edit_children"),
                "level_types": {str(k): v for k, v in level_types.items()},
                "can_edit": level >= self._action_level(K.A_EDIT_CHILDREN) and not node["locked"],
                "can_add": level >= self._action_level(K.A_ADD_CHILD) and not node["locked"],
                "can_delete": level >= self._action_level(K.A_DELETE) and not node["locked"],
                "can_govern": level >= GOVERNOR,
                "allowed_child_types": [{"uid": a, "name": cat.namer()(a)} for a in allowed],
                "default_types": allowed[:1] or [K.NT_FOLDER]}

    def children_values(self, user: User, uid: str, column: dict, q: str = "", limit: int = PAGE) -> list[dict]:
        """First `limit` distinct values (+counts) of one grid column over all children, for its filter."""
        cat = self._cat()
        node = self._node(uid)
        if self._level(user, chain_of(self.repo, node)) < READER:
            raise Forbidden("no access")
        base, arg = parse_ptype(column.get("ptype") or "String")
        name_of = cat.namer()
        refs = base in ("Enum", "Link") or (base == "Array" and arg not in ("String", "OwnProperty"))
        out = []
        for v, c in self.repo.distinct_values(node, column, q="" if refs else q, limit=1000 if refs and q else limit):
            label = name_of(v) if refs and v else v
            if refs and q and q.lower() not in (label or "").lower():
                continue
            out.append({"value": v, "label": label, "count": c})
            if len(out) >= limit:
                break
        return out

    def edit_children(self, user: User, uid: str, changes: list[dict], deletes: list[str] | None = None) -> dict:
        """changes: [{uid?: str, parent_uid?: str, types?: [...], values: {prop: text}, expected?: ts}]."""
        cat = self._cat()
        top = self._node(uid)
        chain = chain_of(self.repo, top)
        level = self._need_action(user, chain, K.A_EDIT_CHILDREN)
        self._not_locked(top)
        resolve = cat.resolver()
        gov = sorted({k for c in changes for k in list((c.get("values") or {})) + list(c.get("user_defined") or {})
                      if k in ("Owner", "Steward")})
        if gov and level < GOVERNOR:
            raise Forbidden(f"changing {', '.join(gov)} needs Governor (you have {LEVEL_NAMES[level]})")
        wanted = {c["uid"] for c in changes if c.get("uid")} | {c["parent_uid"] for c in changes if c.get("parent_uid")}
        wanted |= set(deletes or [])
        known = {k: v for k, v in self.repo.get_many(sorted(wanted - {uid})).items() if uid in v["path"]}
        known[uid] = top
        out_nodes: dict[str, dict] = {}
        expected: dict[str, str] = {}
        errors: list[dict] = []
        creates = [c for c in changes if not c.get("uid") or c["uid"] not in known]
        for what, act, n_ in (("adding rows", K.A_ADD_CHILD, len(creates)), ("deleting rows", K.A_DELETE, len(deletes or []))):
            need = self._action_level(act)
            if n_ and level < need:
                raise Forbidden(f"{what} needs {LEVEL_NAMES[need]} (you have {LEVEL_NAMES[level]})")
        nums = iter(self.repo.allocate(len(creates)) if creates else [])
        chains: dict[str, list[dict]] = {uid: chain}
        for i, ch in enumerate(changes):
            try:
                values = dict(ch.get("values") or {})
                values.pop("UID", None)
                if ch.get("uid") in known and ch.get("uid") != uid:
                    cur = out_nodes.get(ch["uid"]) or known[ch["uid"]]
                    if cur["locked"]:
                        raise RuleError(f"'{cur['name']}' is locked")
                    if lk.is_link(cur):
                        other = sorted(k for k in values if k not in lk.EDITABLE_OWN)
                        if other:
                            raise RuleError(f"{', '.join(other)}: '{cur['name']}' is a Child Link — change it on the origin node")
                    if "Types" in values:
                        tmp = self._apply(cur, {"Types": values["Types"]}, cat.def_map(cur), resolve, False)
                        defs = {**cat.def_map(cur), **cat.def_map(tmp)}
                    else:
                        defs = cat.def_map(cur)
                    out_nodes[cur["uid"]] = self._apply(cur, values, defs, resolve, True, ch.get("user_defined"))
                    if ch.get("expected"):
                        expected[cur["uid"]] = ch["expected"]
                else:
                    pid = ch.get("parent_uid") or uid
                    parent = out_nodes.get(pid) or known.get(pid)
                    if parent is None:
                        raise RuleError("parent is not in this table")
                    if pid not in chains:
                        chains[pid] = chain_of(self.repo, parent)
                    pchain = chains[pid]
                    types = self._parse("Array(NodeType)", ch.get("types") or values.pop("Types", None)
                                        or (allowed_child_types(pchain)[:1] or [K.NT_FOLDER]), resolve)
                    values.pop("Types", None)
                    check_child_types(pchain, types, cat.namer())
                    check_depth(pchain, 1)
                    name = str(values.pop("Name", "") or "").strip()
                    if not name:
                        raise RuleError("Name is required for a new row")
                    n = new_node(next(nums), name, types, parent, origin="manual", modified_by=user.name)
                    out_nodes[n["uid"]] = self._apply(n, values, cat.def_map(n), resolve, False)
                    known[n["uid"]] = out_nodes[n["uid"]]
            except RuleError as e:
                errors.append({"row": i, "uid": ch.get("uid"), "error": str(e),
                               "field": getattr(e, "field", None)})
        doomed = []
        for d in deletes or []:
            if d in known and d != uid:
                if known[d]["locked"] or self.repo.subtree_stats(known[d])["locked"]:
                    errors.append({"row": None, "uid": d, "error": f"'{known[d]['name']}' or a node below it is locked"})
                    continue
                doomed.append(known[d])
        if errors:
            return {"saved": 0, "errors": errors}
        for n in out_nodes.values():
            self._sync_kind(n)
        self.repo.upsert(list(out_nodes.values()), user.name, expected or None)
        if any((n["properties"] or {}).get("kind") == "Enum" for n in out_nodes.values()):
            cat.invalidate()
        removed = sum(self.repo.soft_delete_subtree(d, True, user.name) for d in doomed)
        return {"saved": len(out_nodes) + removed, "errors": []}

