"""Pipeline updates started by the jobs wait behind a running update instead of failing."""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdm.pipeline_runs import run_update  # noqa: E402


class FakePipelines:
    def __init__(self, busy_polls: int, race: bool = False):
        self.busy, self.race, self.started, self.polls = busy_polls, race, [], 0

    def list_updates(self, pid, max_results=1):
        self.busy -= 1
        state = "RUNNING" if self.busy >= 0 else "COMPLETED"
        return NS(updates=[NS(update_id="other", state=NS(value=state))])

    def start_update(self, pid, **kw):
        if self.race:
            self.race = False
            raise RuntimeError("An active update already exists")
        self.started.append(kw)
        return NS(update_id="mine")

    def get_update(self, pid, uid):
        self.polls += 1
        return NS(update=NS(state=NS(value="RUNNING" if self.polls < 2 else "COMPLETED")))


def _run(p, monkeypatch):
    import mdm.pipeline_runs as pr
    monkeypatch.setattr(pr.time, "sleep", lambda s: None)
    return run_update("p1", NS(pipelines=p), log=lambda m: None, refresh_selection=["x"])


def test_waits_for_a_running_update_then_starts(monkeypatch):
    p = FakePipelines(busy_polls=2)
    assert _run(p, monkeypatch) == "COMPLETED" and p.started == [{"refresh_selection": ["x"]}]


def test_retries_when_another_update_starts_in_between(monkeypatch):
    p = FakePipelines(busy_polls=0, race=True)
    assert _run(p, monkeypatch) == "COMPLETED" and len(p.started) == 1


def test_write_retries_on_a_delta_conflict(monkeypatch):
    import mdm.sqlrepo as sr
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)

    class Flaky(sr.SqlExecutor):
        def __init__(self):
            self.calls = 0

        def query(self, sql, params=None):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("[DELTA_CONCURRENT_APPEND] ConcurrentAppendException: files were added")
            return [{"num_affected_rows": 1}]

    x = Flaky()
    assert x.execute("MERGE INTO t USING s ON 1=1") == {"num_affected_rows": 1} and x.calls == 3
    y = Flaky()
    import pytest
    with pytest.raises(RuntimeError):   # a read is not retried
        y.execute("SELECT 1")
