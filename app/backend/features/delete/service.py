"""Delete and Delete All Child."""

from __future__ import annotations

from mdm.hierarchy import RuleError, chain_of, check_child_types, check_depth
from mdm.model import K
from mdm.permissions import User


class DeleteService:
    def delete(self, user: User, uid: str, mode: str = "tree") -> dict:
        """mode: 'tree' deletes the whole subtree; 'move' re-parents the children to the node's parent."""
        node = self._node(uid)
        if node["uid"] == K.ROOT:
            raise RuleError("the root cannot be deleted")
        chain = chain_of(self.repo, node)
        self._need_action(user, chain, K.A_DELETE)
        self._not_locked(node)
        stats = self.repo.subtree_stats(node)
        if mode == "move" and stats["count"]:
            cat = self._cat()
            parent = chain[-2]
            self._not_locked(parent)
            kid_types = [v for v, _ in self.repo.distinct_values(node, {"name": "Types", "ptype": "Array(NodeType)"}, limit=500)]
            for t in kid_types:
                if t:
                    check_child_types(chain[:-1], [t], cat.namer())
            check_depth(chain[:-1], stats["height"])
            moved = self.repo.lift_children(node, user.name)
            self.repo.upsert([dict(node, is_deleted=True)], user.name)
            self._definitions_changed(node)
            return {"deleted": 1, "moved": moved, "parent": node["parent_uid"]}
        if stats["locked"]:
            raise RuleError(f"{stats['locked']} locked node(s) below — unlock them first")
        if K.NT_EXPORT in node["types"]:
            self._drop_export_object(node)
        gone = self.repo.soft_delete_subtree(node, True, user.name)
        self._definitions_changed(node)
        return {"deleted": gone, "parent": node["parent_uid"]}

    def delete_all_children(self, user: User, uid: str) -> dict:
        node = self._node(uid)
        chain = chain_of(self.repo, node)
        self._need_action(user, chain, K.A_DELETE_ALL_CHILDREN)
        self._not_locked(node)
        if self.repo.subtree_stats(node)["locked"]:
            raise RuleError("some children are locked")
        gone = self.repo.soft_delete_subtree(node, False, user.name)
        self._definitions_changed(node, children=True)
        return {"deleted": gone}

