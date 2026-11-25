"""Child Link nodes: a node (NodeType Child Link) that shows another node, its origin, in a second place.

A link node is a real row in MDMNodes under its own parent. Its own values are its position and governance:
Parent, Owner, Steward, Max Child Depth, Allowed Child Type, Locked, and the link itself (Linked Node, Link
Type). Everything else — Name, Types, Icon and every property — is the origin's, shown read-only and changed
only on the origin. It has its own children (not the origin's). Golden records link their source records with
Link Type "source"; Add Child Link and Browse → Link make the others.
"""

from __future__ import annotations

from .model import K, new_node

LINKED = "Linked Node"
LINK_TYPE = "Link Type"
KIND = "Link"
# the link node's own rows; all other rows of a link node come from the origin
OWN_PROPERTIES = {"UID", "Types", "Parent", "Origin", "Last Modified", "Max Child Depth", "Allowed Child Type",
                  "Owner", "Steward", "Locked", LINKED, LINK_TYPE}
EDITABLE_OWN = {"Max Child Depth", "Allowed Child Type", "Owner", "Steward", LINK_TYPE}


def is_link(node: dict | None) -> bool:
    return bool(node) and K.NT_CHILD_LINK in (node.get("types") or [])


def target_of(node: dict | None) -> str | None:
    return ((node or {}).get("properties") or {}).get(LINKED) if is_link(node) else None


def new_link(uid_num: int, parent: dict, origin: dict, link_type_uid: str | None, *, node_origin: str = "manual",
             locked: bool = False, max_child_depth: int | None = None) -> dict:
    props = {"kind": KIND, LINKED: origin["uid"]}
    if link_type_uid:
        props[LINK_TYPE] = link_type_uid
    return new_node(uid_num, origin["name"], [K.NT_CHILD_LINK], parent, properties=props, origin=node_origin,
                    locked=locked, max_child_depth=max_child_depth)


def as_row(link: dict, origin: dict | None, keep_uid: bool = True) -> dict:
    """A Child Link node as a row of a grid or export: the origin's name, types and properties with the link's
    own position and governance values. keep_uid=False shows the origin's UID (exports)."""
    if origin is None:
        return dict(link, _link=True, _via=link["uid"])
    row = dict(origin)
    for k in ("parent_uid", "path", "depth", "sort_order", "locked", "owner", "steward", "max_child_depth",
              "allowed_child_types", "modified_at", "modified_by", "is_deleted"):
        if k in link:
            row[k] = link[k]
    if keep_uid:
        row.update(uid=link["uid"], uid_num=link["uid_num"])
    row["_link"], row["_via"] = True, link["uid"]
    return row


def shown(node: dict, origins: dict[str, dict]) -> dict:
    """The node as the hierarchy shows it: a link node takes the origin's name, types and icon."""
    o = origins.get(target_of(node) or "")
    if not is_link(node) or not o:
        return node
    return dict(node, name=o["name"], icon=o.get("icon"), _origin_types=o["types"], _origin=o)


def convert_child_links(repo, user: str = "mdm_init", link_types: dict[str, str] | None = None) -> int:
    """Release upgrade: every entry of the old child_links arrays becomes a Child Link node under the same
    parent (same order, same link type); the arrays are emptied. Idempotent."""
    parents = [n for n in repo.with_child_links()]
    if not parents:
        return 0
    targets = repo.get_many(sorted({l["uid"] for p in parents for l in p["child_links"]}))
    entries = [(p, l) for p in parents for l in sorted(p["child_links"], key=lambda x: x.get("sort_order") or 0)
               if l["uid"] in targets]
    nums = repo.allocate(len(entries)) if entries else []
    lt = {k.lower(): v for k, v in (link_types or {}).items()}
    writes, cleared = [], {}
    for (p, l), num in zip(entries, nums):
        golden = (l.get("link_type") or "").lower() == "source"
        n = new_link(num, p, targets[l["uid"]], lt.get((l.get("link_type") or "").lower()),
                     node_origin="generated" if golden else "manual", locked=golden, max_child_depth=0 if golden else None)
        n["sort_order"] = l.get("sort_order")
        writes.append(n)
    for p in parents:
        cleared[p["uid"]] = dict(p, child_links=[])
    repo.upsert(writes + list(cleared.values()), user)
    return len(writes)
