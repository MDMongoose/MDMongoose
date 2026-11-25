"""Start a Lakeflow pipeline update from a job task and wait for it, queueing behind a running update.

A pipeline has at most one active update; starting another one fails. The jobs therefore wait until the
running update (from another job or a click) ends and then start theirs, instead of failing.
"""

from __future__ import annotations

import time

DONE = ("COMPLETED", "FAILED", "CANCELED")


def _active(w, pipeline_id: str) -> str | None:
    """The update that is running now, if any."""
    try:
        ups = w.pipelines.list_updates(pipeline_id, max_results=1).updates or []
    except Exception:  # noqa: BLE001 - if the list fails, starting tells us
        return None
    u = ups[0] if ups else None
    state = u.state.value if u and u.state else None
    return u.update_id if state and state not in DONE else None


def run_update(pipeline_id: str, w=None, poll: int = 10, max_wait: int = 2 * 3600, log=print, **start_kw) -> str:
    """Starts an update (start_kw: refresh_selection, full_refresh_selection, ...) once no other update runs,
    waits for it and returns its final state (COMPLETED | FAILED | CANCELED)."""
    if w is None:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
    deadline = time.time() + max_wait
    while True:
        busy = _active(w, pipeline_id)
        if busy:
            log(f"pipeline {pipeline_id}: update {busy} is running — waiting for it")
        else:
            try:
                upd = w.pipelines.start_update(pipeline_id, **start_kw)
                break
            except Exception as e:  # noqa: BLE001 - another update started in between: wait again
                if "active" not in str(e).lower() and "conflict" not in type(e).__name__.lower():
                    raise
                log(f"pipeline {pipeline_id}: {e} — waiting")
        if time.time() > deadline:
            raise TimeoutError(f"pipeline {pipeline_id} stayed busy for {max_wait // 60} minutes")
        time.sleep(poll * 3)
    log(f"pipeline {pipeline_id}: update {upd.update_id} started")
    while True:
        u = w.pipelines.get_update(pipeline_id, upd.update_id).update
        state = u.state.value if u and u.state else "UNKNOWN"
        if state in DONE:
            return state
        time.sleep(poll)
