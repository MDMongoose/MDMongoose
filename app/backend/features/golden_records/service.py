"""Refresh Golden Records: the dry run."""

from __future__ import annotations

from mdm.hierarchy import chain_of
from mdm.permissions import READER, User


class GoldenRecordsService:
    def golden_plan(self, user: User, uid: str, threshold: float | None = None, retire: bool = True) -> dict:
        from mdm import golden
        node = self._node(uid)
        self._need(user, chain_of(self.repo, node), READER)
        return golden.public(golden.plan(self.repo, self.catalog, uid, threshold, retire))

