"""Stable Golden UIDs: a regrouped entity keeps the UID most of its records already had.

Clusters are handled largest first; each takes the UID with the most votes among its records'
previous Golden UIDs that no larger cluster took (ties: the oldest UID, i.e. the smallest number).
Clusters left without a UID get a new one from the UID counter. UIDs that no cluster keeps are
returned as `released` — survivorship retires their golden records.
"""

from __future__ import annotations

from ..uid import from_uid, to_uid


def _num(uid: str) -> int:
    try:
        return from_uid(uid)
    except ValueError:
        return 1 << 62


def order_key(g: set[str]) -> tuple[int, str]:
    """Clusters are handled in this order: largest first, then by their smallest record UID."""
    return -len(g), min(g)


def pick(groups: list[set[str]], previous: dict[str, str | None]) -> tuple[list[tuple[str | None, set[str]]], set[str]]:
    """The kept UID of every cluster (None: it needs a new one), in handling order; and the UIDs kept.
    Clusters that share no previous UID do not influence each other, so Distributed processing runs this per
    group of clusters that do and gets the same answer."""
    used: set[str] = set()
    out: list[tuple[str | None, set[str]]] = []
    for g in sorted(groups, key=order_key):
        votes: dict[str, int] = {}
        for r in g:
            k = previous.get(r)
            if k:
                votes[k] = votes.get(k, 0) + 1
        k = next((k for k, _ in sorted(votes.items(), key=lambda kv: (-kv[1], _num(kv[0]))) if k not in used), None)
        if k:
            used.add(k)
        out.append((k, g))
    return out, used


def assign(groups: list[set[str]], previous: dict[str, str | None], allocate) -> tuple[list[tuple[str, set[str], bool]], set[str]]:
    """groups: sets of record uids; previous: record uid -> previous Golden UID (or None);
    allocate(n) -> n new uid numbers. Returns ([(golden uid, members, is_new)], released uids)."""
    picked, used = pick(groups, previous)
    out: list[tuple[str | None, set[str], bool]] = [(k, g, k is None) for k, g in picked]
    need = [i for i, (k, _, _) in enumerate(out) if k is None]
    if need:
        nums = allocate(len(need))
        for i, n in zip(need, nums):
            out[i] = (to_uid(n), out[i][1], True)
    before = {k for k in previous.values() if k}
    return out, before - used
