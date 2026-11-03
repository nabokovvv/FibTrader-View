"""Optional plugins.

Every sub-package of ``app.plugins`` is loaded at import time and may expose:

* ``router`` — a FastAPI ``APIRouter``, mounted under ``/api``;
* ``startup(state)`` / ``shutdown(state)`` — called from the app lifespan with ``app.state``
  (after the candle service, symbols and user DB exist; before the hub starts).

A plugin that fails to import is logged and skipped, so a missing private plugin never breaks the app.
The public build simply ships without the private plugin folders.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType

log = logging.getLogger("lv.plugins")


def _discover() -> list[ModuleType]:
    mods: list[ModuleType] = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if not info.ispkg:
            continue
        try:
            mods.append(importlib.import_module(f"{__name__}.{info.name}"))
        except Exception:
            log.exception("plugin %s failed to load, skipped", info.name)
    return mods


PLUGINS: list[ModuleType] = _discover()


def names() -> list[str]:
    return [m.__name__.rsplit(".", 1)[-1] for m in PLUGINS]


def startup(state) -> None:
    for m in PLUGINS:
        if hasattr(m, "startup"):
            m.startup(state)


def shutdown(state) -> None:
    for m in reversed(PLUGINS):
        if hasattr(m, "shutdown"):
            try:
                m.shutdown(state)
            except Exception:
                log.exception("plugin %s shutdown failed", m.__name__)


def routers():
    return [m.router for m in PLUGINS if getattr(m, "router", None) is not None]
