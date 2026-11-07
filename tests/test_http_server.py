"""Streamable HTTP transport: same tools as stdio, transport security, /health, and the stateless-safety guard."""

import inspect
import re
import typing

import pytest
from mcp import Client

from conftest import CONFIG_DIR, ROOT, SMX_DIR
from dma.config import load_settings
from dma.server import build_server, http_app
from dma.tools import DmaTools
from httpkit import http_client, mcp_client

pytestmark = pytest.mark.anyio


def make_settings(tmp_path, **extra):
    return load_settings({"DMA_SMX_DIR": str(SMX_DIR), "DMA_OUTPUT_DIR": str(tmp_path / "output"), "DMA_CONFIG_DIR": str(CONFIG_DIR), "DMA_TRANSPORT": "http", **extra})


def make_app(tmp_path, **extra):
    settings = make_settings(tmp_path, **extra)
    return http_app(build_server(DmaTools(settings)), settings.http)


async def test_http_lists_the_same_tools_as_stdio(tmp_path):
    settings = make_settings(tmp_path)
    async with Client(build_server(DmaTools(settings))) as c:
        local = sorted(t.name for t in (await c.list_tools()).tools)
    async with mcp_client(make_app(tmp_path)) as c:
        remote = sorted(t.name for t in (await c.list_tools()).tools)
    assert remote == local and len(remote) == 28


async def test_http_runs_a_tool(tmp_path):
    async with mcp_client(make_app(tmp_path)) as c:
        result = await c.call_tool("run_rules", {"model_name": "synthetic_fleet_eip"})
    assert not result.is_error and result.structured_content["findings"]


async def test_health_needs_no_auth_and_leaks_nothing(tmp_path, monkeypatch):
    secret = "s3cr3t-value-that-must-not-appear-0123456789"
    for name in ("DMA_DEV_TOKEN", "PBI_ACCESS_TOKEN", "DMA_ENTRA_CLIENT_SECRET", "DMA_DBT_GIT_TOKEN"):
        monkeypatch.setenv(name, secret)
    monkeypatch.setenv("DMA_GIT_COMMIT", "abc1234")
    async with http_client(make_app(tmp_path)) as hc:
        r = await hc.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "version", "commit"}
    assert body["status"] == "ok" and body["commit"] == "abc1234"
    assert secret not in r.text and str(tmp_path) not in r.text


async def test_unknown_host_is_refused(tmp_path):
    async with http_client(make_app(tmp_path)) as hc:
        r = await hc.post("/mcp", headers={"host": "evil.example.test", "content-type": "application/json", "accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 421


async def test_unknown_origin_is_refused(tmp_path):
    async with http_client(make_app(tmp_path)) as hc:
        r = await hc.post("/mcp", headers={"origin": "https://evil.example.test", "content-type": "application/json", "accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 403


async def test_allowed_host_from_settings(tmp_path):
    app = make_app(tmp_path, DMA_ALLOWED_HOSTS="dma.example.test")
    async with http_client(app) as hc:
        ok = await hc.post("/mcp", headers={"host": "dma.example.test", "content-type": "application/json", "accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        loopback = await hc.post("/mcp", headers={"content-type": "application/json", "accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert ok.status_code != 421 and loopback.status_code == 421


# Stateless JSON mode has no channel for server-to-client requests. This guard fails as soon as a tool starts to need one.
SERVER_TO_CLIENT = re.compile(r"\b(elicit\w*|create_message|report_progress|send_progress\w*|sample)\s*\(")


def test_no_tool_uses_elicitation_sampling_or_progress():
    from dma.server import TOOLS

    for name, _, _ in TOOLS:
        fn = getattr(DmaTools, name)
        hints = typing.get_type_hints(fn, include_extras=True)
        for pname, param in inspect.signature(fn).parameters.items():
            hint = str(hints.get(pname, ""))
            assert "Context" not in hint and pname not in ("ctx", "context"), f"{name} takes an MCP context ({pname})"
    for path in (ROOT / "src" / "dma" / "tools.py", ROOT / "src" / "dma" / "server.py"):
        hits = [m.group(0) for m in SERVER_TO_CLIENT.finditer(path.read_text(encoding="utf-8"))]
        # self.sampler.sample(...) is the row sampler, not MCP sampling.
        hits = [h for h in hits if not h.startswith("sample")]
        assert not hits, f"{path.name} calls {hits}"


def test_http_refuses_to_start_without_auth(tmp_path, monkeypatch):
    from dma import server

    monkeypatch.setattr(server, "load_dotenv", lambda *a, **k: None)
    for k, v in {"DMA_TRANSPORT": "http", "DMA_SMX_DIR": str(SMX_DIR), "DMA_OUTPUT_DIR": str(tmp_path / "o"), "DMA_CONFIG_DIR": str(CONFIG_DIR)}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DMA_AUTH", raising=False)
    with pytest.raises(SystemExit, match="DMA_AUTH"):
        server.main()
