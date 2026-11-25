"""Add Child (and new NodeTypes)."""

from __future__ import annotations

from typing import Any

from mdm.hierarchy import RuleError, chain_of, check_child_types, check_depth
from mdm.model import K, new_node
from mdm.permissions import User


class AddChildService:
    def add_child(self, user: User, parent_uid: str, name: str, types: list[str],
                  values: dict[str, Any] | None = None) -> dict:
        cat = self._cat()
        parent = self._node(parent_uid)
        chain = chain_of(self.repo, parent)
        self._need_action(user, chain, K.A_ADD_CHILD)
        self._not_locked(parent)
        resolve = cat.resolver()
        type_uids = self._parse("Array(NodeType)", types or [K.NT_FOLDER], resolve)
        check_child_types(chain, type_uids, cat.namer())
        check_depth(chain, 1)
        if not (name or "").strip():
            raise RuleError("Name cannot be empty")
        num = self.repo.allocate(1)[0]
        n = new_node(num, name.strip(), type_uids, parent, origin="manual", modified_by=user.name)
        n = self._apply(n, values or {}, cat.def_map(n), resolve, False)
        self._check_governance(user, chain, set(values or {}), cat.def_map(n))
        self._sync_kind(n)
        if K.NT_EXPORT in n["types"]:
            self._prepare_export(user, n, None)
        self.repo.upsert([n], user.name)
        if (n["properties"] or {}).get("kind") or parent["uid"] == K.SYS_PERMISSIONS:
            cat.invalidate()   # a definition or a permission node
        cat.forget_values()    # a new Enum value / Link target can be typed by name at once
        if K.NT_EXPORT in n["types"]:
            self._after_export_saved(user, n, None)
        return {"uid": n["uid"]}

    def create_node_type(self, user: User, parent_uid: str, name: str, default_icon: str, defs: list[dict],
                         actions: list[str], new_action: dict | None = None) -> dict:
        parent = self._node(parent_uid or K.MODEL_NODE_TYPES)
        chain = chain_of(self.repo, parent)
        self._need_action(user, chain, K.A_ADD_CHILD)
        self._not_locked(parent)
        if not (name or "").strip():
            raise RuleError("Name is required")
        if self._cat().type_uid(name.strip()):
            raise RuleError(f"a NodeType named {name} already exists")
        acts = list(actions or [])
        if new_action and (new_action.get("name") or "").strip():
            acts.append(self.create_action(user, new_action["name"], new_action.get("handler", ""),
                                           new_action.get("group") or "Self")["uid"])
        num = self.repo.allocate(1)[0]
        n = new_node(num, name.strip(), [K.NT_FOLDER], parent, icon=default_icon or "box", max_child_depth=0,
                     properties={"kind": "NodeType", "default_icon": default_icon or "box",
                                 "property_defs": self._check_defs(defs), "actions": acts})
        self.repo.upsert([n], user.name)
        self.catalog.invalidate()
        return {"uid": n["uid"]}

