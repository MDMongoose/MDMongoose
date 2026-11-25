"""Add Child Link: a Child Link node under the parent that shows an existing node (its origin)."""

from __future__ import annotations

from mdm import links as lk
from mdm.hierarchy import RuleError, chain_of, check_child_types, check_depth
from mdm.model import K
from mdm.permissions import User


class ChildLinkService:
    def add_child_link(self, user: User, parent_uid: str, link_uid: str, link_type: str | None = None,
                       position: str = "last") -> dict:
        cat = self._cat()
        parent, linked = self._node(parent_uid), self._node(link_uid)
        if lk.is_link(linked):   # a link to a link shows the same origin
            linked = self._node(lk.target_of(linked))
        chain = chain_of(self.repo, parent)
        self._need_action(user, chain, K.A_ADD_CHILD_LINK)
        self._not_locked(parent)
        if linked["uid"] == parent["uid"] or linked["uid"] in parent["path"]:
            raise RuleError("cannot link an ancestor as a child")
        if linked["parent_uid"] == parent["uid"]:
            raise RuleError("already a child here")
        if any(lk.target_of(c) == linked["uid"] for c in self.repo.links_under([parent["uid"]])):
            raise RuleError(f"'{linked['name']}' is already linked here")
        check_child_types(chain, linked["types"], cat.namer())
        check_depth(chain, 1)
        lt = self._parse("Enum(LinkTypes)", link_type, cat.resolver()) if link_type else None
        node = lk.new_link(self.repo.allocate(1)[0], parent, linked, lt)
        if position == "first":
            first = self.repo.children(parent, limit=1)
            node["sort_order"] = ((first[0].get("sort_order") or 1.0) - 1.0) if first else None
        self.repo.upsert([node], user.name)
        return {"uid": node["uid"], "target": linked["uid"]}

    def remove_child_link(self, user: User, parent_uid: str, link_uid: str) -> dict:
        """Delete the Child Link node `link_uid` (or the link under parent_uid to the origin `link_uid`)."""
        parent = self._node(parent_uid)
        node = self.repo.get(link_uid)
        if not lk.is_link(node):
            node = next((c for c in self.repo.links_under([parent["uid"]]) if lk.target_of(c) == link_uid), None)
        if not node or node["parent_uid"] != parent["uid"]:
            raise RuleError("no such child link here")
        self._need_action(user, chain_of(self.repo, node), K.A_DELETE)
        self._not_locked(node)
        self.repo.soft_delete_subtree(node, True, user.name)
        return {"deleted": node["uid"]}
