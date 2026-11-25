"""Warm start and keep-warm for the app's reads.

Warm start: when the app starts, the top levels of the hierarchy and the pages of their nodes are read once, so
the app's SQL cache already holds them for the first user (the cached statements are the same for every user;
permissions are applied in Python). The same happens again when a job's write emptied the cache.

Keep-warm (off by default): MDM_KEEP_WARM="08:00-18:00" sends a tiny query to the SQL warehouse every few
minutes inside that window (MDM_KEEP_WARM_TZ, default UTC; MDM_KEEP_WARM_DAYS, default "Mon-Fri"), so a
serverless warehouse with a short auto-stop does not go to sleep during working hours. It costs warehouse time.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time

from mdm.permissions import User

log = logging.getLogger("mdm.warm")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
EVERY = 240        # seconds between keep-warm queries (well below the 5-10 minute auto-stop)
LEVELS = 4         # levels of the hierarchy read at warm start
PAGES = 120        # property pages read at warm start (the first nodes of those levels)
_started = False


def warm_once(svc) -> int:
    """Reads the top levels and the first property pages; returns how many pages were read."""
    admin = User("mdm-warm-start", {"admins"}, is_admin=True)
    p = svc.prefetch(admin, levels=LEVELS)
    uids = list(p["items"])[:PAGES]
    for i in range(0, len(uids), 40):
        svc.node_views(admin, uids[i:i + 40])
    return len(uids)


def _days(spec: str) -> set[int]:
    out = set()
    for part in (spec or "Mon-Fri").replace(" ", "").split(","):
        a, _, b = part.partition("-")
        ia = DAYS.index(a[:3].title())
        ib = DAYS.index(b[:3].title()) if b else ia
        out.update(range(ia, ib + 1) if ia <= ib else list(range(ia, 7)) + list(range(0, ib + 1)))
    return out


def in_window(now: dt.datetime, window: str, days: str) -> bool:
    """window "HH:MM-HH:MM" on the given days (now in the window's time zone)."""
    a, _, b = window.partition("-")
    start, end = dt.time.fromisoformat(a.strip()), dt.time.fromisoformat(b.strip())
    return now.weekday() in _days(days) and start <= now.time() < end


def _now(tz: str) -> dt.datetime:
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(tz))
    except Exception:  # noqa: BLE001 - unknown zone: UTC
        return dt.datetime.now(dt.timezone.utc)


def _cache_size(svc) -> int | None:
    x = getattr(getattr(svc, "repo", None), "x", None)
    data = getattr(x, "_data", None)
    return len(data) if data is not None else None


def _ping(svc) -> None:
    x = getattr(getattr(svc, "repo", None), "x", None)
    if x is not None:
        (getattr(x, "query_fresh", None) or x.query)("SELECT 1 AS ok")


def start(get_svc) -> None:
    """Starts the background thread once (warm start, then keep-warm if configured)."""
    global _started
    if _started or os.getenv("MDM_LOCAL") == "1" or os.getenv("MDM_WARM_START", "true").lower() == "false":
        return
    _started = True
    window = os.getenv("MDM_KEEP_WARM", "").strip()
    tz = os.getenv("MDM_KEEP_WARM_TZ", "UTC")
    days = os.getenv("MDM_KEEP_WARM_DAYS", "Mon-Fri")

    def run() -> None:
        try:
            t = time.time()
            n = warm_once(get_svc())
            log.info("warm start: %s pages in %.1fs", n, time.time() - t)
        except Exception as e:  # noqa: BLE001 - the app works without it
            log.warning("warm start failed: %s", e)
        while True:
            time.sleep(EVERY)
            try:
                svc = get_svc()
                if window and in_window(_now(tz), window, days):
                    _ping(svc)
                    size = _cache_size(svc)
                    if size is not None and size < 20:   # a job's write emptied the cache: fill it again
                        warm_once(svc)
            except Exception as e:  # noqa: BLE001
                log.warning("keep-warm: %s", e)

    threading.Thread(target=run, name="mdm-warm", daemon=True).start()
