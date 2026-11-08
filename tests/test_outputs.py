"""Getting outputs back to the client: text inline, every file as an MCP resource, writes still only after final approval."""

import base64
import io
import json
from pathlib import Path

import openpyxl
import pytest
from mcp import Client

from conftest import CONFIG_DIR, SMX_DIR
from dma.config import load_settings
from dma.server import build_http, build_server
from dma.tools import DmaTools
from httpkit import mcp_client

pytestmark = pytest.mark.anyio

WAVE3 = ["Bronze", "Silver - Staging", "Silver - IDM", "GOLD - SDM", "Lookup", "Report Table Mapping"]
DEV_TOKEN = "dev-token-for-tests-only-0123456789abcdef"


def settings_for(tmp_path):
    return load_settings({"DMA_SMX_DIR": str(SMX_DIR), "DMA_OUTPUT_DIR": str(tmp_path / "output"), "DMA_CONFIG_DIR": str(CONFIG_DIR)})


async def approve_all(c, flow):
    started = (await c.call_tool("start_session", {"flow": flow, "scope": "outputs test"})).structured_content
    sid = started["session_id"]
    for st in started["stages"]:
        args = {"session_id": sid, "stage": st["stage"], "content": f"output of {st['stage']}"}
        if st["stage"] == "bronze_identification":
            args.update(bronze_tables=["brnz_fleetops.booking"], table_status={"booking": "dbt_model_found"})
        v = (await c.call_tool("submit_stage", args)).structured_content
        await c.call_tool("record_decision", {"session_id": sid, "stage": st["stage"], "version": v["version"], "decision": "approve"})
    return sid


async def test_compile_report_returns_text_and_uris(tmp_path):
    async with Client(build_server(DmaTools(settings_for(tmp_path)))) as c:
        sid = await approve_all(c, "lineage")
        arts = (await c.call_tool("compile_report", {"session_id": sid})).structured_content["artifacts"]
        for a in arts:
            assert a["uri"] == f"dma://artifacts/{sid}/{Path(a['path']).name}"
            assert a["content"] == Path(a["path"]).read_text(encoding="utf-8")
            read = await c.read_resource(a["uri"])
            assert read.contents[0].text == a["content"]
    report = next(a for a in arts if a["kind"] == "findings_md")
    decisions = next(a for a in arts if a["kind"] == "decisions_json")
    assert report["content"].startswith("# Lineage report")
    assert json.loads(decisions["content"])["session_id"] == sid


async def test_write_artifact_returns_text_kinds_inline(tmp_path):
    async with Client(build_server(DmaTools(settings_for(tmp_path)))) as c:
        sid = await approve_all(c, "lineage")
        w = (await c.call_tool("write_artifact", {"kind": "lineage_mermaid", "name": "lineage", "content": "flowchart LR\n  a --> b\n", "session_id": sid})).structured_content
    assert w["content"] == "flowchart LR\n  a --> b\n" and w["uri"].startswith(f"dma://artifacts/{sid}/")


async def test_workbook_comes_back_as_a_blob_resource(tmp_path):
    async with Client(build_server(DmaTools(settings_for(tmp_path)))) as c:
        sid = await approve_all(c, "build")
        w = (await c.call_tool("write_artifact", {"kind": "proposed_smx_xlsx", "name": "model", "content": "", "session_id": sid})).structured_content
        req = (await c.call_tool("write_artifact", {"kind": "requirements_md", "name": "requirements", "content": "", "session_id": sid})).structured_content
        assert w["content"] is None and w["uri"] == f"dma://workbooks/{sid}/{Path(w['path']).name}"
        read = await c.read_resource(w["uri"])
        templates = (await c.list_resource_templates()).resource_templates
    blob = read.contents[0]
    assert blob.mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    data = base64.b64decode(blob.blob)
    assert data == Path(w["path"]).read_bytes()
    assert openpyxl.load_workbook(io.BytesIO(data), read_only=True).sheetnames == WAVE3
    assert req["content"].startswith("# Report requirements")
    assert {t.uri_template for t in templates} == {"dma://artifacts/{session_id}/{file_name}", "dma://workbooks/{session_id}/{file_name}"}


async def test_resources_refuse_files_the_session_did_not_write(tmp_path):
    tools = DmaTools(settings_for(tmp_path))
    async with Client(build_server(tools)) as c:
        sid = await approve_all(c, "build")
        w = (await c.call_tool("write_artifact", {"kind": "proposed_smx_xlsx", "name": "model", "content": "", "session_id": sid})).structured_content
        name = Path(w["path"]).name
        # A file dropped next to it was never recorded by the session, so it is not served.
        (Path(w["path"]).parent / "20990101T000000000000Z_other.md").write_text("not yours", encoding="utf-8")
        cases = [
            (f"dma://artifacts/{sid}/20990101T000000000000Z_other.md", "has no artifact named"),
            (f"dma://artifacts/{sid}/{name}", "is served from dma://workbooks/"),
            (f"dma://workbooks/{sid}/../{name}", "Unknown resource"),
            (f"dma://artifacts/{'0' * 32}/{name}", "unknown session"),
        ]
        for uri, reason in cases:
            with pytest.raises(Exception, match=reason):
                await c.read_resource(uri)


async def test_nothing_is_written_or_served_before_final_approval(tmp_path):
    async with Client(build_server(DmaTools(settings_for(tmp_path)))) as c:
        sid = (await c.call_tool("start_session", {"flow": "lineage", "scope": "early"})).structured_content["session_id"]
        early = await c.call_tool("compile_report", {"session_id": sid})
    assert early.is_error
    out = tmp_path / "output"
    assert [p.name for p in out.iterdir()] == [".sessions"]


async def test_outputs_come_back_over_http_with_the_dev_token(tmp_path):
    env = {"DMA_SMX_DIR": str(SMX_DIR), "DMA_OUTPUT_DIR": str(tmp_path / "output"), "DMA_CONFIG_DIR": str(CONFIG_DIR), "DMA_TRANSPORT": "http", "DMA_AUTH": "dev", "DMA_DEV_TOKEN": DEV_TOKEN, "DMA_DEV_USER_ID": "dev-user", "DMA_RESOURCE_SERVER_URL": "http://127.0.0.1:8000/mcp"}
    async with mcp_client(build_http(load_settings(env), env), headers={"authorization": f"Bearer {DEV_TOKEN}"}) as c:
        sid = await approve_all(c, "build")
        arts = (await c.call_tool("compile_report", {"session_id": sid})).structured_content["artifacts"]
        w = (await c.call_tool("write_artifact", {"kind": "proposed_smx_xlsx", "name": "model", "content": "", "session_id": sid})).structured_content
        blob = (await c.read_resource(w["uri"])).contents[0].blob
    assert all(a["content"] for a in arts)
    assert openpyxl.load_workbook(io.BytesIO(base64.b64decode(blob)), read_only=True).sheetnames == WAVE3
    assert Path(w["path"]).parent == (tmp_path / "output" / "dev-user").resolve()
