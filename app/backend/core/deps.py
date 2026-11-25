"""Request dependencies shared by every router: the service, the background runner and the current user."""

from __future__ import annotations

from fastapi import Request

from mdm.permissions import User

from .config import Runner, build_service, current_user

_svc = None
_runner = None


def svc():
    global _svc, _runner
    if _svc is None:
        _svc = build_service()
        _runner = Runner(_svc)
        _svc.export_hook = lambda uid, user: _runner.start("job:mdm_export", uid, user)
    return _svc


def runner() -> Runner:
    svc()
    return _runner


def me(request: Request, as_group: str | None = None) -> User:
    """The signed-in user; admins can view the app as another UC group."""
    u = current_user(request.headers)
    if as_group and u.is_admin:
        return User(u.name, {as_group}, is_admin=False)
    return u
