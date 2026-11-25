"""The node model: one row of MDMNodes, plus well-known seeded UIDs."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from .uid import to_uid

# Columns of mdm_core.mdmnodes (properties is VARIANT in Delta, a dict here).
NODE_COLUMNS = [
    "uid", "uid_num", "name", "types", "parent_uid", "path", "depth", "sort_order",
    "child_links", "origin", "source_table_uid", "source_key", "record_hash",
    "properties", "user_defined", "icon", "max_child_depth", "allowed_child_types",
    "owner", "steward", "locked", "is_deleted", "modified_at", "modified_by",
]

# Node-level properties (defined by the base NodeType "Node") live in columns.
NODE_PROPERTY_COLUMNS = {
    "UID": "uid",
    "Name": "name",
    "Types": "types",
    "Parent": "parent_uid",
    "Origin": "origin",
    "Last Modified": "modified_at",
    "Icon": "icon",
    "Max Child Depth": "max_child_depth",
    "Allowed Child Type": "allowed_child_types",
    "Owner": "owner",
    "Steward": "steward",
    "Locked": "locked",
    "Order": "sort_order",  # defined by the Ordered type, stored as a column
    "Source Key": "source_key",  # defined by the Record type, stored as columns
    "Record Hash": "record_hash",
}
# Matching writes Golden UID / Match Score / Matched By / Matched At (decisions go through Match Pairs)
MATCHING_PROPERTIES = {"Golden UID", "Match Score", "Matched By", "Matched At"}
READ_ONLY_PROPERTIES = {"UID", "Origin", "Last Modified", "Parent", "Locked"} | MATCHING_PROPERTIES
# Properties that are system-maintained, so the UserDefined flag does not apply.
NO_USER_DEFINED = {"UID", "Origin", "Last Modified", "Parent", "Locked", "Types"} | MATCHING_PROPERTIES


class K:
    """Well-known seeded UIDs. Numbers <= 100 000 are reserved for seeded nodes."""

    ROOT = to_uid(1)
    SYSTEM = to_uid(2)
    SYS_PROPERTY_TYPES = to_uid(3)
    SYS_ACTIONS = to_uid(4)
    SYS_NODE_TYPES = to_uid(5)
    SYS_PERMISSIONS = to_uid(6)
    UID_COUNTER = to_uid(7)

    # PropertyTypes
    PT = {name: to_uid(10 + i) for i, name in enumerate([
        "UID", "Number", "Float", "String", "Timestamp", "Link", "Enum", "Array",
        "Expression", "Icon", "Boolean", "OwnProperty"])}

    # System actions
    A_ADD_CHILD = to_uid(30)
    A_EDIT = to_uid(31)
    A_DELETE = to_uid(32)
    A_LOCK = to_uid(33)
    A_CHANGE_PARENT = to_uid(34)
    A_ADD_CHILD_LINK = to_uid(35)
    A_EDIT_CHILDREN = to_uid(36)
    A_DELETE_ALL_CHILDREN = to_uid(37)
    A_EXPORT = to_uid(38)

    # System NodeTypes
    NT_NODE = to_uid(50)
    NT_FOLDER = to_uid(51)
    NT_ORDERED = to_uid(52)
    NT_TABLE = to_uid(53)
    NT_RECORD = to_uid(54)
    NT_PERMISSION = to_uid(55)
    NT_ENUM = to_uid(56)
    NT_ACTION = to_uid(57)
    NT_CHILD_LINK = to_uid(58)

    # Model (seeded, reserved range)
    MODEL = to_uid(1000)
    MODEL_TABLES = to_uid(1001)
    MODEL_NODE_TYPES = to_uid(1002)
    MODEL_ACTIONS = to_uid(1003)
    MODEL_ENUMS = to_uid(1004)
    MODEL_RULES = to_uid(1005)
    MODEL_EXPORTS = to_uid(1006)
    NT_MASTER_DATA = to_uid(1100)
    NT_SURVIVORSHIP_RULE = to_uid(1101)
    NT_PROPERTY_RULE = to_uid(1102)
    NT_EXPORT = to_uid(1103)
    NT_MATCH_RULE = to_uid(1104)
    NT_MATCH_FIELD = to_uid(1105)
    NT_FUZZY_RULE = to_uid(1106)
    NT_MATCH_PAIR = to_uid(1107)
    NT_STANDARDIZATION_TABLE = to_uid(1108)
    A_CONFIGURE_LOAD = to_uid(1200)
    A_LOAD_CHILDREN = to_uid(1201)
    A_REFRESH_GOLDEN = to_uid(1202)
    A_EDIT_RULE = to_uid(1203)
    A_RUN_EXPORT = to_uid(1204)
    A_EDIT_MATCH_RULE = to_uid(1205)
    A_TRAIN_MATCHING = to_uid(1206)
    A_RUN_MATCHING = to_uid(1207)
    A_REVIEW_MATCHES = to_uid(1208)
    A_RECORD_MATCHING = to_uid(1209)
    A_GOLDEN_MATCHING = to_uid(1210)
    REFERENCE_DATA = to_uid(1400)
    MASTER_DATA = to_uid(1401)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_node(uid_num: int, name: str, types: list[str], parent: dict | None, **kw: Any) -> dict:
    """Build a node dict with path/depth derived from its parent."""
    path = (list(parent["path"]) + [parent["uid"]]) if parent else []
    node = {
        "uid": to_uid(uid_num),
        "uid_num": uid_num,
        "name": name,
        "types": list(types),
        "parent_uid": parent["uid"] if parent else None,
        "path": path,
        "depth": len(path),
        "sort_order": None,
        "child_links": [],
        "origin": "manual",
        "source_table_uid": None,
        "source_key": None,
        "record_hash": None,
        "properties": {},
        "user_defined": {},
        "icon": None,
        "max_child_depth": None,
        "allowed_child_types": [],
        "owner": None,
        "steward": [],
        "locked": False,
        "is_deleted": False,
        "modified_at": now_iso(),
        "modified_by": "system",
    }
    for key, value in kw.items():
        if key not in node:
            raise KeyError(f"unknown node column {key}")
        node[key] = value
    return node


def path_key_of(path: list[str], uid: str) -> str:
    """Compact, prefix-searchable location of a node: '/1/2/7/' for MDM-1 / MDM-2 / MDM-7.
    MDMNodes is clustered by it, so a subtree (LIKE '<key>%') and the children of a node
    read only the files of that branch."""
    return "/" + "/".join(u[4:] if u.startswith("MDM-") else u for u in list(path) + [uid]) + "/"


def path_key(node: dict) -> str:
    return path_key_of(node.get("path") or [], node["uid"])


def kind_of(node: dict) -> str | None:
    """Definition nodes are marked so they can be found without scanning the hierarchy."""
    p = node.get("properties") or {}
    if p.get("kind"):
        return str(p["kind"])
    return "Action" if p.get("handler") else None


def clone(node: dict) -> dict:
    return copy.deepcopy(node)


def get_prop(node: dict, name: str) -> Any:
    col = NODE_PROPERTY_COLUMNS.get(name)
    if col:
        return node.get(col)
    return (node.get("properties") or {}).get(name)


def set_prop(node: dict, name: str, value: Any) -> None:
    col = NODE_PROPERTY_COLUMNS.get(name)
    if col:
        node[col] = value
    else:
        node.setdefault("properties", {})[name] = value


def parse_ptype(ptype: str) -> tuple[str, str | None]:
    """'Enum(Countries)' -> ('Enum', 'Countries'); 'String' -> ('String', None)."""
    if "(" in ptype and ptype.endswith(")"):
        base, arg = ptype.split("(", 1)
        return base.strip(), arg[:-1].strip()
    return ptype.strip(), None
