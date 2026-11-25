"""Effective permissions.

A node's Steward property lists Permission nodes (Group + Level). The first
steward list from the root down grants levels; every lower steward list can only
narrow them (a group must be listed again, and gets min(previous, new)).

Levels, each including the ones before it:
  Nothing     does not see the node
  Reader      sees the node and all its properties; runs only Reader actions (Export)
  Modifier    + edits properties of unlocked nodes (not the Governance group)
  Supervisor  + locks / unlocks
  Manager     + adds children, changes the parent, deletes
  Governor    + edits the Governance group (Owner, Steward)
  Admin       everything on every node (the admins group / MDM_ADMINS; cannot be granted per node)
Every action has a Required Level; only users at or above it can run it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .uid import to_uid

NONE, READER, MODIFIER, SUPERVISOR, MANAGER, GOVERNOR, ADMIN = range(7)
READ, MODIFY = READER, MODIFIER  # older names
LEVEL_NAMES = {NONE: "Nothing", READER: "Reader", MODIFIER: "Modifier", SUPERVISOR: "Supervisor",
               MANAGER: "Manager", GOVERNOR: "Governor", ADMIN: "Admin"}
LEVEL_BY_NAME = {v.lower(): k for k, v in LEVEL_NAMES.items()}
LEVEL_BY_NAME.update({"none": NONE, "read": READER, "modify": MODIFIER})
# values of the PermissionLevels enum (Model / Enums); 1341/1342 were Read/Modify
LEVEL_UIDS = {READER: to_uid(1341), MODIFIER: to_uid(1342), SUPERVISOR: to_uid(1343), MANAGER: to_uid(1344),
              GOVERNOR: to_uid(1345), ADMIN: to_uid(1346)}
LEVEL_BY_UID = {u: lvl for lvl, u in LEVEL_UIDS.items()}
GRANTABLE = (READER, MODIFIER, SUPERVISOR, MANAGER, GOVERNOR)  # Admin comes from the admins group only


def level_of(value) -> int:
    """A level from an enum UID, a level name or a number; unknown → NONE."""
    if value is None:
        return NONE
    if isinstance(value, int):
        return value
    v = str(value).strip()
    if v in LEVEL_BY_UID:
        return LEVEL_BY_UID[v]
    return LEVEL_BY_NAME.get(v.lower(), NONE)


def _granted(pn: dict) -> int:
    return min(LEVEL_BY_UID.get((pn.get("properties") or {}).get("Level"), NONE), GOVERNOR)


@dataclass
class User:
    name: str
    groups: set[str] = field(default_factory=set)
    is_admin: bool = False

    @property
    def principals(self) -> set[str]:
        return {self.name, *self.groups}


def effective_levels(chain: list[dict], perm_nodes: dict[str, dict]) -> dict[str, int]:
    """chain = ancestors from root to the node itself."""
    levels: dict[str, int] | None = None
    for n in chain:
        if not n.get("steward"):
            continue
        here: dict[str, int] = {}
        for p in n["steward"]:
            pn = perm_nodes.get(p)
            if not pn:
                continue
            g = pn["properties"].get("Group")
            lvl = _granted(pn)
            if g:
                here[g] = max(here.get(g, NONE), lvl)
        if levels is None:
            levels = here
        else:
            levels = {g: min(levels[g], lvl) for g, lvl in here.items() if g in levels}
    return levels or {}


def level_for(user: User, chain: list[dict], perm_nodes: dict[str, dict]) -> int:
    if user.is_admin:
        return ADMIN
    levels = effective_levels(chain, perm_nodes)
    return max([levels.get(p, NONE) for p in user.principals] or [NONE])


def perm_uids(chain: list[dict]) -> list[str]:
    return sorted({p for n in chain for p in (n.get("steward") or [])})


def effective_detail(chain: list[dict], perm_nodes: dict[str, dict]) -> dict[str, dict]:
    """Per group: {level, from (node), note} — for the Steward property's effective table."""
    out: dict[str, dict] = {}
    started = False
    for n in chain:
        if not n.get("steward"):
            continue
        here: dict[str, int] = {}
        for p in n["steward"]:
            pn = perm_nodes.get(p)
            if pn and pn["properties"].get("Group"):
                g = pn["properties"]["Group"]
                here[g] = max(here.get(g, NONE), _granted(pn))
        if not started:
            out = {g: {"level": lvl, "from": n, "note": ""} for g, lvl in here.items()}
            started = True
            continue
        for g in list(out):
            if g not in here:
                if out[g]["level"] > NONE:
                    out[g] = {"level": NONE, "from": n, "note": f"not listed on {n['name']} — access removed below it"}
            elif here[g] < out[g]["level"]:
                out[g] = {"level": here[g], "from": n,
                          "note": f"capped to {LEVEL_NAMES[here[g]]} by {n['name']} (children can only narrow)"}
            elif here[g] > out[g]["level"]:
                out[g]["note"] = f"{n['name']} grants {LEVEL_NAMES[here[g]]}, but children can only narrow"
        for g in here:
            if g not in out:
                out[g] = {"level": NONE, "from": n, "note": f"granted on {n['name']}, but not above it — children can only narrow"}
    return out
