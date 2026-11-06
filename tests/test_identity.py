"""The gate user comes from the caller resolver: the DMA_USER label in stdio, the verified token in HTTP mode."""

import json

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from conftest import CONFIG_DIR, SMX_DIR
from dma.config import load_settings
from dma.identity import Caller, CallerUnknown
from dma.server import build_server
from dma.tools import DmaTools


def make_tools(tmp_path, caller=None, user="modeller.one"):
    settings = load_settings({"DMA_SMX_DIR": str(SMX_DIR), "DMA_OUTPUT_DIR": str(tmp_path / "output"), "DMA_CONFIG_DIR": str(CONFIG_DIR), "DMA_USER": user})
    return DmaTools(settings, caller=caller)


def events(tools, sid):
    return [json.loads(line) for line in tools.writer.session_log(sid).read_text(encoding="utf-8").splitlines()]


def run_one_stage(tools):
    sid = tools.start_session("lineage", "identity test").session_id
    stage = tools.get_session(sid).current_stage
    tools.submit_stage(sid, stage, "stage output")
    tools.record_decision(sid, stage, 1, "approve")
    return sid


def test_stdio_keeps_the_v1_log_shape(tmp_path):
    tools = make_tools(tmp_path)
    sid = run_one_stage(tools)
    log = events(tools, sid)
    assert all(e["user"] == "modeller.one" for e in log)
    assert not any("user_id" in e for e in log)
    assert "owner" not in log[0]
    assert tools.get_session(sid).decision_log[0].user_id is None


def test_verified_caller_is_recorded_on_every_event(tmp_path):
    caller = Caller(id="11111111-1111-1111-1111-111111111111", label="Modeller One (11111111-1111-1111-1111-111111111111)")
    tools = make_tools(tmp_path, caller=lambda: caller, user="ignored.dma.user")
    sid = run_one_stage(tools)
    log = events(tools, sid)
    assert log[0]["owner"] == caller.id
    assert all(e["user"] == caller.label and e["user_id"] == caller.id for e in log)
    view = tools.get_session(sid)
    assert view.created_by == caller.label
    assert view.decision_log[0].user == caller.label and view.decision_log[0].user_id == caller.id


def test_unknown_caller_writes_nothing(tmp_path):
    def nobody():
        raise CallerUnknown("no verified caller for this request")

    tools = make_tools(tmp_path, caller=nobody)
    with pytest.raises(ToolError, match="no verified caller"):
        tools.start_session("lineage", "identity test")
    assert tools.writer.list_session_ids() == []


@pytest.mark.anyio
async def test_no_tool_takes_a_user_argument(tmp_path):
    async with Client(build_server(make_tools(tmp_path))) as c:
        tools = (await c.list_tools()).tools
    for t in tools:
        props = set((t.input_schema or {}).get("properties", {}))
        assert not props & {"user", "user_id", "owner", "caller"}, t.name
