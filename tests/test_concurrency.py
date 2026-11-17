"""Concurrent gate calls on one session: the per-session lock keeps replay, checks and append together."""

import json
import threading
import time

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from conftest import CONFIG_DIR, SMX_DIR
from dma.config import load_settings
from dma.tools import DmaTools
from dma.writer import ArtifactWriter


@pytest.fixture
def tools(tmp_path, monkeypatch):
    real = ArtifactWriter.read_session_events

    def slow_read(self, session_id):
        # Widen the window between reading the log and appending to it, so an unlocked race would show.
        events = real(self, session_id)
        time.sleep(0.05)
        return events

    monkeypatch.setattr(ArtifactWriter, "read_session_events", slow_read)
    settings = load_settings({"DMA_SMX_DIR": str(SMX_DIR), "DMA_OUTPUT_DIR": str(tmp_path / "output"), "DMA_CONFIG_DIR": str(CONFIG_DIR)})
    return DmaTools(settings)


def race(n, fn):
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def run(i):
        barrier.wait()
        try:
            results[i] = ("ok", fn(i))
        except ToolError as e:
            results[i] = ("error", str(e))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return results


def log_lines(tools, sid):
    return [json.loads(line) for line in tools.writer.session_log(sid).read_text(encoding="utf-8").splitlines()]


def test_concurrent_record_decision_leaves_one_decision(tools):
    sid = tools.start_session("lineage", "race test").session_id
    stage = tools.get_session(sid).current_stage
    tools.submit_stage(sid, stage, "stage output")
    results = race(2, lambda i: tools.record_decision(sid, stage, 1, "approve") if i == 0 else tools.record_decision(sid, stage, 1, "suggest_changes", note="change it"))
    oks = [r for r in results if r[0] == "ok"]
    errors = [r for r in results if r[0] == "error"]
    assert len(oks) == 1 and len(errors) == 1
    assert "already" in errors[0][1]
    decisions = [e for e in log_lines(tools, sid) if e["type"] == "decision"]
    assert len(decisions) == 1
    # The log replays cleanly, and the stage shows the one decision that won.
    view = tools.get_session(sid)
    first = view.stages[0]
    assert first.versions[0].status == ("approved" if decisions[0]["decision"] == "approve" else "changes_requested")


def test_concurrent_submit_leaves_one_version(tools):
    sid = tools.start_session("lineage", "race test").session_id
    stage = tools.get_session(sid).current_stage
    results = race(3, lambda i: tools.submit_stage(sid, stage, f"output {i}"))
    assert sum(r[0] == "ok" for r in results) == 1
    submitted = [e for e in log_lines(tools, sid) if e["type"] == "stage_submitted"]
    assert [e["version"] for e in submitted] == [1]


def test_lock_is_reentrant_in_one_thread(tmp_path):
    writer = ArtifactWriter(tmp_path / "output")
    sid = "a" * 32
    writer.append_session_event(sid, {"type": "session_started"}, create=True)
    with writer.session_lock(sid):
        with writer.session_lock(sid):
            writer.append_session_event(sid, {"type": "x"})
    assert len(writer.read_session_events(sid)) == 2


def test_lock_refuses_a_bad_session_id(tmp_path):
    writer = ArtifactWriter(tmp_path / "output")
    with pytest.raises(ValueError, match="32 lowercase hex"):
        with writer.session_lock("../escape"):
            pass
