"""Browse: move, clone, link and delete many subtrees at once, with left-out branches."""

from __future__ import annotations

from mdm.hierarchy import RuleError, chain_of, check_child_types, check_depth
from mdm.model import K, clone
from mdm.uid import to_uid
from mdm import links as lk

UPSERT_BATCH = 2000
NOT_CLONABLE_KINDS = ("NodeType", "PropertyType", "Action")


class Selection:
    def __init__(self, repo, selected: list[str], left_out: list[str]):
        marks = {u.upper(): True for u in selected or []}
        for u in left_out or []:
            marks.setdefault(u.upper(), False)
        nodes = repo.get_many(list(marks))
        self.nodes = {u: n for u, n in nodes.items()}
        self.marks = {u: m for u, m in marks.items() if u in self.nodes}
        self.roots: list[dict] = []
        self.branches: list[dict] = []
        for u, n in self.nodes.items():
            inherited = self._above(n)
            if self.marks[u] and not inherited:
                self.roots.append(n)
            elif not self.marks[u] and inherited:
                self.branches.append(n)
        self.roots.sort(key=lambda n: (n["depth"], n["name"]))
        self.branches.sort(key=lambda n: (n["depth"], n["name"]))
        if not self.roots:
            raise RuleError("nothing selected")
        if any(r["uid"] == K.ROOT for r in self.roots):
            raise RuleError("the root MDM cannot be moved, cloned, linked or deleted")

    def _above(self, n: dict) -> bool:
        """Is the node's parent in the selection (nearest marked ancestor)?"""
        for a in reversed(n["path"]):
            if a in self.marks:
                return self.marks[a]
        return False

    def state(self, n: dict) -> bool:
        if n["uid"] in self.marks:
            return self.marks[n["uid"]]
        return self._above(n)

    def root_of(self, n: dict) -> dict | None:
        for a in reversed(n["path"] + [n["uid"]]):
            r = next((r for r in self.roots if r["uid"] == a), None)
            if r:
                return r
        return None

    def branches_of(self, root: dict) -> list[dict]:
        return [b for b in self.branches if self.root_of(b) is root]


class BrowseService:
    # ------------------------------------------------------------------ counts and plan
    def _sel(self, body: dict) -> Selection:
        self._cat()
        return Selection(self.repo, body.get("selected") or [], body.get("left_out") or [])

    def _sizes(self, s: Selection) -> dict:
        nodes = sum(1 + self.repo.subtree_stats(r)["count"] for r in s.roots)
        out = sum(1 + self.repo.subtree_stats(b)["count"] for b in s.branches)
        return {"roots": len(s.roots), "nodes": nodes - out, "child_elements": nodes - out - len(s.roots),
                "left_out_branches": len(s.branches), "left_out_nodes": out}

    def manage_count(self, user, body: dict) -> dict:
        s = self._sel(body)
        for r in s.roots:
            self._need(user, chain_of(self.repo, r), 1)
        return self._sizes(s)

    def _link_plan(self, s: Selection, root: dict) -> list[dict]:
        """The nodes Link mirrors for one selected root: the root and every selected node below it (left-out
        branches are skipped), parents before children. Each becomes a Child Link node under the link of its parent."""
        sub = [n for n in self.repo.subtree(root["uid"]) if s.state(n) and s.root_of(n) is root]
        sub.sort(key=lambda n: (n["depth"], n.get("sort_order") is None, n.get("sort_order") or 0, n["uid_num"]))
        return [root] + sub

    def _stay_parent(self, s: Selection, branch: dict) -> dict:
        root = s.root_of(branch)
        return self._node(root["parent_uid"])

    def _check(self, user, op: str, s: Selection, target: dict | None) -> list[dict]:
        """Validate everything before anything changes; returns the nodes to link (link only)."""
        cat = self._cat()
        name_of = cat.namer()
        tchain = chain_of(self.repo, target) if target else None
        if target:
            self._not_locked(target)
            need = {"move": K.A_ADD_CHILD, "clone": K.A_ADD_CHILD, "link": K.A_ADD_CHILD_LINK}[op]
            self._need_action(user, tchain, need)
        links: list[dict] = []
        for r in s.roots:
            chain = chain_of(self.repo, r)
            if op == "move":
                self._need_action(user, chain, K.A_CHANGE_PARENT)
                self._not_locked(r)
            elif op == "delete":
                self._need_action(user, chain, K.A_DELETE)
                self._not_locked(r)
            else:
                self._need(user, chain, 1)
            if target and op in ("move", "clone"):
                if target["uid"] == r["uid"] or r["uid"] in target["path"] and s.state(target):
                    raise RuleError(f"the new parent is inside the selection '{r['name']}'")
                if op == "move" and target["uid"] == r["parent_uid"] and len(s.roots) == 1:
                    raise RuleError(f"'{target['name']}' is already the parent of '{r['name']}'")
                check_child_types(tchain, r["types"], name_of)
                check_depth(tchain, 1 + self.repo.subtree_stats(r)["height"])
            if op == "clone":
                for n in [r] + self.repo.subtree(r["uid"]):
                    kind = (n.get("properties") or {}).get("kind")
                    if not s.state(n):
                        continue
                    if kind in NOT_CLONABLE_KINDS:
                        raise RuleError(f"'{n['name']}' cannot be cloned ({kind}) — leave it out")
                    unique = next((lbl for t, lbl in ((K.NT_TABLE, "a Table: a copy would load the same source again"),
                                                      (K.NT_EXPORT, "an Export node: its view or file name must be unique"),
                                                      (K.NT_MATCH_RULE, "a Match Rule: a table belongs to one Match Rule"))
                                   if t in n["types"]), None)
                    if unique:
                        raise RuleError(f"'{n['name']}' cannot be cloned — it is {unique}. Leave it out, or clone its children")
            if op == "link":
                plan = self._link_plan(s, r)
                for n in plan:
                    if target["uid"] == n["uid"] or n["uid"] in target["path"]:
                        raise RuleError(f"cannot link '{n['name']}' under its own descendant")
                    origin = self._node(lk.target_of(n)) if lk.is_link(n) else n
                    check_child_types(tchain, origin["types"], name_of)
                check_depth(tchain, 1 + max(n["depth"] for n in plan) - r["depth"])
                links += plan
        if op in ("move", "delete"):
            for b in s.branches:
                stay = self._stay_parent(s, b)
                self._not_locked(b)
                sc = chain_of(self.repo, stay)
                self._need_action(user, chain_of(self.repo, b), K.A_CHANGE_PARENT)
                check_child_types(sc, b["types"], name_of)
                check_depth(sc, 1 + self.repo.subtree_stats(b)["height"])
        if op == "delete":
            for r in s.roots:
                locked = self.repo.subtree_stats(r)["locked"] - sum(
                    self.repo.subtree_stats(b)["locked"] + (1 if b["locked"] else 0) for b in s.branches_of(r))
                if locked > 0:
                    raise RuleError(f"{locked} locked node(s) in '{r['name']}' — unlock them or leave them out")
        return links

    def manage_plan(self, user, op: str, body: dict) -> dict:
        """Counts and the sentence for the confirmation modal; `problem` when the action is not possible."""
        if op not in ("move", "clone", "link", "delete"):
            raise RuleError("unknown action")
        s = self._sel(body)
        sizes = self._sizes(s)
        target = self._node(body["target"]) if body.get("target") else None
        out = {**sizes, "op": op, "target": {"uid": target["uid"], "name": target["name"]} if target else None,
               "roots_list": [{"uid": r["uid"], "name": r["name"]} for r in s.roots[:20]],
               "stay_under": sorted({self._stay_parent(s, b)["name"] for b in s.branches}) if op in ("move", "delete") else []}
        if op != "delete" and not target:
            out["problem"] = "pick the new parent"
            return out
        try:
            links = self._check(user, op, s, target)
            if op == "link":
                out["links"] = [{"uid": n["uid"], "name": n["name"]} for n in links[:30]]
                out["link_count"] = len(links)
        except RuleError as e:
            out["problem"] = str(e)
        return out

    def manage_parents(self, user, body: dict, q: str = "") -> list[dict]:
        """Candidate parents for move / clone / link, each with the reason when it cannot take the selection."""
        cat = self._cat()
        op = body.get("op") or "move"
        s = self._sel(body)
        if q:
            cands = self.repo.search(q, 60)
        else:
            first = s.roots[0]
            chain = chain_of(self.repo, first)
            cands = list(chain[:-1])
            for c in chain[-3:-1]:
                cands += self.repo.children(c, limit=40)
        out, seen = [], set()
        heights = {r["uid"]: self.repo.subtree_stats(r)["height"] for r in s.roots}
        for c in cands:
            if c["uid"] in seen or (c.get("properties") or {}).get("kind") in ("NodeType", "PropertyType"):
                continue
            seen.add(c["uid"])
            why = ""
            ch = chain_of(self.repo, c)
            try:
                if c["locked"]:
                    raise RuleError("locked")
                for r in s.roots:
                    if op != "link" and (c["uid"] == r["uid"] or r["uid"] in c["path"]) and s.state(c):
                        raise RuleError(f"inside the selection “{r['name']}”")
                if op == "move" and all(r["parent_uid"] == c["uid"] for r in s.roots):
                    raise RuleError("already their parent")
                for r in s.roots:
                    check_child_types(ch, r["types"], cat.namer())
                    if op != "link":
                        check_depth(ch, 1 + heights[r["uid"]])
                need = K.A_ADD_CHILD_LINK if op == "link" else K.A_ADD_CHILD
                if self._level(user, ch) < self._action_level(need):
                    raise RuleError("no permission")
            except RuleError as e:
                why = str(e).split(" (set on")[0]
            out.append({"uid": c["uid"], "name": c["name"], "path": " / ".join(x["name"] for x in ch[:-1]),
                        "icon": c.get("icon") or cat.default_icon(c["types"]), "problem": why})
        out.sort(key=lambda x: bool(x["problem"]))
        return out[:80]

    # ------------------------------------------------------------------ apply
    def manage_apply(self, user, op: str, body: dict) -> dict:
        s = self._sel(body)
        target = self._node(body["target"]) if body.get("target") else None
        if op != "delete" and not target:
            raise RuleError("pick the new parent")
        sizes = self._sizes(s)
        links = self._check(user, op, s, target)
        if op in ("move", "delete"):
            for b in s.branches:
                stay = self._stay_parent(s, b)
                self.repo.move_subtree(self._node(b["uid"]), self._node(stay["uid"]), user.name)
        if op == "move":
            for r in s.roots:
                self.repo.move_subtree(self._node(r["uid"]), self._node(target["uid"]), user.name)
            return {"moved": sizes["nodes"], "kept": sizes["left_out_nodes"], "target": target["uid"]}
        if op == "delete":
            gone = 0
            for r in s.roots:
                node = self._node(r["uid"])
                if K.NT_EXPORT in node["types"]:
                    self._drop_export_object(node)
                gone += self.repo.soft_delete_subtree(node, True, user.name)
            return {"deleted": gone, "kept": sizes["left_out_nodes"]}
        if op == "link":
            # the selection is mirrored: a Child Link node per selected node, under the link of its parent
            p = self._node(target["uid"])
            have = {lk.target_of(c) for c in self.repo.links_under([p["uid"]])}
            origins = self.repo.get_many(sorted({lk.target_of(n) for n in links if lk.is_link(n)}))
            nums = iter(self.repo.allocate(len(links)) if links else [])
            made: dict[str, dict] = {}
            new, skipped = [], set()
            for n in links:
                o = origins.get(lk.target_of(n)) if lk.is_link(n) else n
                parent = made.get(n["parent_uid"])
                if parent is None:   # a selected root: directly under the target, once per origin
                    if o is None or n["parent_uid"] in skipped or o["uid"] in have or o["parent_uid"] == p["uid"]:
                        skipped.add(n["uid"])
                        continue
                    parent = p
                    have.add(o["uid"])
                if o is None:
                    skipped.add(n["uid"])
                    continue
                ln = lk.new_link(next(nums), parent, o, None)
                ln["sort_order"] = n.get("sort_order")
                made[n["uid"]] = ln
                new.append(ln)
            for i in range(0, len(new), UPSERT_BATCH):
                self.repo.upsert(new[i:i + UPSERT_BATCH], user.name)
            return {"linked": len(new), "target": target["uid"]}
        return self._clone(user, s, target, (body.get("name") or "").strip())

    def _clone(self, user, s: Selection, target: dict, name: str) -> dict:
        """Copy the selected nodes with new UIDs; links inside the copy point to the copies."""
        plan: list[tuple[dict, dict]] = []  # (original, new parent placeholder uid)
        for r in s.roots:
            sub = [n for n in self.repo.subtree(r["uid"]) if s.state(n) and s.root_of(n) is r]
            sub.sort(key=lambda n: (n["depth"], n.get("sort_order") or 0, n["uid_num"]))
            plan.append((r, None))
            plan += [(n, None) for n in sub]
        nums = self.repo.allocate(len(plan))
        new_uid = {orig["uid"]: to_uid(num) for (orig, _), num in zip(plan, nums)}
        by_new: dict[str, dict] = {}
        out = []
        for (orig, _), num in zip(plan, nums):
            n = clone(orig)
            nu = new_uid[orig["uid"]]
            is_root = any(r["uid"] == orig["uid"] for r in s.roots)
            parent = target if is_root else by_new[new_uid[orig["parent_uid"]]]
            n.update(uid=nu, uid_num=num, parent_uid=parent["uid"], path=list(parent["path"]) + [parent["uid"]],
                     origin="manual", source_key=None, source_table_uid=None, record_hash=None, locked=False,
                     is_deleted=False)
            n["depth"] = len(n["path"])
            n.pop("path_key", None)
            if is_root and name and len(s.roots) == 1:
                n["name"] = name
            props = n.get("properties") or {}
            if lk.is_link(n) and props.get(lk.LINKED) in new_uid:   # a link inside the copy shows the copy
                props[lk.LINKED] = new_uid[props[lk.LINKED]]
            for k in ("_members", "_lineage", "_match_hash", "Golden UID", "Match Score", "Matched By", "Matched At"):
                props.pop(k, None)
            by_new[nu] = n
            out.append(n)
        for i in range(0, len(out), UPSERT_BATCH):
            self.repo.upsert(out[i:i + UPSERT_BATCH], user.name)
        roots_new = [new_uid[r["uid"]] for r in s.roots]
        return {"cloned": len(out), "target": target["uid"], "roots": roots_new}
