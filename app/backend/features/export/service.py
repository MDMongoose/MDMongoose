"""Export: CSV / Excel download of a branch."""

from __future__ import annotations

from mdm import export as export_mod
from mdm.hierarchy import RuleError, chain_of
from mdm.model import K
from mdm.permissions import READER, User


class ExportService:
    def export(self, user: User, uid: str, fmt: str = "csv", depth: int = 1, balanced: bool = False,
               options: dict | None = None):
        cat = self._cat()
        node = self._node(uid)
        self._need_action(user, chain_of(self.repo, node), K.A_EXPORT)
        size = self.repo.subtree_stats(node)["count"]
        if size > export_mod.APP_EXPORT_LIMIT:
            raise RuleError(f"{size:,} nodes below — too many to export from the app (limit "
                            f"{export_mod.APP_EXPORT_LIMIT:,}). Export a smaller branch or a lower child depth.")
        opt = export_mod.ExportOptions(format=fmt, depth=depth, balanced=balanced,
                                       **{k: v for k, v in (options or {}).items()
                                          if k in export_mod.ExportOptions.__dataclass_fields__})
        return export_mod.run(self.repo, cat, node, fmt, depth, balanced, opt)

    def export_preview(self, user: User, uid: str, options: dict) -> dict:
        cat = self._cat()
        node = self._node(uid)
        self._need(user, chain_of(self.repo, node), READER)
        opt = export_mod.ExportOptions(**{k: v for k, v in options.items()
                                          if k in export_mod.ExportOptions.__dataclass_fields__})
        out = export_mod.preview(self.repo, cat, node, opt)
        stats = self.repo.subtree_stats(node)
        out["max_depth"], out["subtree_count"] = stats["height"], stats["count"]
        out["app_limit"] = export_mod.APP_EXPORT_LIMIT
        return out

