"""Features: one folder per action dialog (or tool) of the app.

Each folder may hold
    service.py   one class named <Something>Service with the feature's methods (mixed into MdmService)
    api.py       a FastAPI `router` with the feature's endpoints
Folders are found automatically; see app/customization.md.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil


def _modules(kind: str):
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.ispkg:
            try:
                yield importlib.import_module(f"{__name__}.{info.name}.{kind}")
            except ModuleNotFoundError as e:
                if e.name != f"{__name__}.{info.name}.{kind}":
                    raise


def service_classes() -> list[type]:
    out = []
    for mod in _modules("service"):
        out += [c for n, c in inspect.getmembers(mod, inspect.isclass)
                if n.endswith("Service") and c.__module__ == mod.__name__]
    return out


def routers() -> list:
    return [mod.router for mod in _modules("api")]
