"""Steward decisions + constrained union-find.

    must-links    exact keys, Match Pairs with Status "Same entity"
    cannot-links  Match Pairs with Status "Not the same" — they win over everything: a union that would
                  put two cannot-linked records into one entity is skipped (also through a chain A–B–C)
    links         scored pairs in the link band, strongest first
"""

from __future__ import annotations

KIND_ORDER = {"Exact key": 0, "Steward": 1}


class ConstrainedUF:
    def __init__(self, items):
        self.p = {i: i for i in items}
        self.members = {i: {i} for i in items}
        self.cannot: dict[str, set[str]] = {i: set() for i in items}  # root -> records it may never join

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def forbid(self, a, b):
        if a in self.p and b in self.p:
            self.cannot[self.find(a)].add(b)
            self.cannot[self.find(b)].add(a)

    def union(self, a, b) -> bool | None:
        """True joined, None already together, False blocked by a cannot-link."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return None
        if self.cannot[ra] & self.members[rb] or self.cannot[rb] & self.members[ra]:
            return False
        if len(self.members[ra]) < len(self.members[rb]):
            ra, rb = rb, ra
        self.p[rb] = ra
        self.members[ra] |= self.members.pop(rb)
        self.cannot[ra] |= self.cannot.pop(rb)
        return True

    def groups(self) -> list[set[str]]:
        return [set(m) for m in self.members.values()]


def cluster(records: list[str], exact: list[tuple[str, str, str]], same: list[tuple[str, str, float | None]],
            cannot: list[tuple[str, str]], links: list[tuple[str, str, float, str]]):
    """Returns (groups, evidence, blocked):
    groups    list of sets of record uids
    evidence  uid -> (score, matched by) of the strongest accepted edge at that record
    blocked   pairs (a, b, why) that were not joined because of a "Not the same" decision"""
    uf = ConstrainedUF(records)
    for a, b in cannot:
        uf.forbid(a, b)
    ev: dict[str, tuple[float, str]] = {}
    blocked = []

    def note(x, score, kind):
        cur = ev.get(x)
        rank = (score if score is not None else 0.0, -KIND_ORDER.get(kind, 2))
        if cur is None or rank > (cur[0] if cur[0] is not None else 0.0, -KIND_ORDER.get(cur[1], 2)):
            ev[x] = (score, kind)

    def edge(a, b, score, kind):
        if a not in uf.p or b not in uf.p:
            return
        res = uf.union(a, b)
        if res is False:
            blocked.append((a, b, kind))
            return
        note(a, score, kind)
        note(b, score, kind)

    # a fixed order, so the result does not depend on the order the edges were found in (One Machine and
    # Distributed processing give the same entities)
    for a, b, _field in sorted(exact, key=lambda x: (x[0], x[1])):
        edge(a, b, 1.0, "Exact key")
    for a, b, score in sorted(same, key=lambda x: (x[0], x[1])):
        edge(a, b, score, "Steward")
    for a, b, score, kind in sorted(links, key=lambda x: (-x[2], x[0], x[1])):
        edge(a, b, score, kind)
    return uf.groups(), ev, blocked
