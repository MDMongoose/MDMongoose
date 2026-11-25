"""Structural rules: Allowed Child Type, Max Child Depth, moving subtrees."""

from __future__ import annotations

from .repo import NodeRepo


class RuleError(Exception):
    """A structural or validation rule was violated (HTTP 400)."""


def chain_of(repo: NodeRepo, node: dict) -> list[dict]:
    """Ancestors from the root down, plus the node itself."""
    anc = repo.ancestors(node)
    return [anc[u] for u in node["path"] if u in anc] + [node]


def allowed_child_types(chain: list[dict]) -> list[str]:
    """Nearest explicit list wins: empty = no constraint; a list set on a node applies to
    every level below it until a lower node sets its own list."""
    for n in reversed(chain):
        if n.get("allowed_child_types"):
            return list(n["allowed_child_types"])
    return []


def allowed_source(chain: list[dict]) -> dict | None:
    for n in reversed(chain):
        if n.get("allowed_child_types"):
            return n
    return None


def check_child_types(chain: list[dict], types: list[str], name_of=lambda u: u) -> None:
    allowed = allowed_child_types(chain)
    if not allowed:
        return
    if not set(types).intersection(allowed):
        src = allowed_source(chain)
        raise RuleError(f"type not allowed here — allowed: {', '.join(name_of(a) for a in allowed)}"
                        f" (set on {src['name'] if src else '?'})")


def check_depth(chain: list[dict], extra_levels: int = 1) -> None:
    """A new node `extra_levels` below chain[-1] (plus its own subtree) must fit every
    ancestor's Max Child Depth (0 = no children)."""
    target_depth = chain[-1]["depth"] + extra_levels
    for n in chain:
        m = n.get("max_child_depth")
        if m is not None and target_depth - n["depth"] > m:
            raise RuleError(f"'{n['name']}' allows {m} level(s) of children")


def move_subtree(node: dict, new_parent: dict, subtree: list[dict]) -> list[dict]:
    """Return node + subtree with parent/path/depth rewritten under new_parent."""
    if node["uid"] == new_parent["uid"] or node["uid"] in new_parent["path"]:
        raise RuleError("cannot move a node under itself")
    old_prefix = node["path"] + [node["uid"]]
    new_path = new_parent["path"] + [new_parent["uid"]]
    moved = dict(node, parent_uid=new_parent["uid"], path=new_path, depth=len(new_path))
    out = [moved]
    for d in subtree:
        rest = d["path"][len(old_prefix):]
        p = new_path + [node["uid"]] + rest
        out.append(dict(d, path=p, depth=len(p)))
    return out


def subtree_height(node: dict, subtree: list[dict]) -> int:
    return max([d["depth"] - node["depth"] for d in subtree] or [0])
