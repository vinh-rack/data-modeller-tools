import json

import pytest
from mcp import Client

from conftest import CONFIG_DIR, FIXTURES, SMX_DIR
from dma.config import load_settings
from dma.server import build_server
from dma.tools import DmaTools

WRITE_TOOLS = {"start_session", "submit_stage", "record_decision", "compile_report", "write_artifact"}
EXPECTED_TOOLS = {"list_models", "get_model", "find_tables_by_bronze", "find_tables_by_grain", "run_rules", "dbt_upstream", "sql_column_lineage", "get_session", "list_sessions", "list_kb", "read_kb", "get_report_map", "pbi_get_report", "pbi_model_metadata"} | WRITE_TOOLS


@pytest.fixture
def server(tmp_path):
    settings = load_settings({
        "DMA_SMX_DIR": str(SMX_DIR),
        "DMA_OUTPUT_DIR": str(tmp_path / "output"),
        "DMA_CONFIG_DIR": str(CONFIG_DIR),
        "DMA_KB_DIR": str(FIXTURES / "kb"),
        "DMA_DBT_MANIFEST": str(FIXTURES / "dbt" / "manifest.json"),
    })
    # No live Power BI in tests: the report usage test patches model_metadata, so the client is never used.
    return build_server(DmaTools(settings, pbi_factory=object))


@pytest.mark.anyio
async def test_lists_tools_with_read_only_hints(server):
    async with Client(server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    for name, t in tools.items():
        assert t.description
        assert t.annotations.read_only_hint is (name not in WRITE_TOOLS)


@pytest.mark.anyio
async def test_run_rules_returns_findings(server):
    async with Client(server) as c:
        r = await c.call_tool("run_rules", {"model_name": "synthetic_fleet_eip"})
    data = r.structured_content
    rule_ids = {f["rule_id"] for f in data["findings"]}
    assert {"LAYER-001", "FACT-001", "NAME-001", "GRAIN-001", "GRAIN-002"} <= rule_ids
    for f in data["findings"]:
        assert {"rule_id", "severity", "layer", "table", "column", "message", "evidence"} <= set(f)


@pytest.mark.anyio
async def test_run_rules_subset_and_unknown_table(server):
    async with Client(server) as c:
        ok = await c.call_tool("run_rules", {"model_name": "synthetic_fleet_eip", "tables": ["f_booking"]})
        bad = await c.call_tool("run_rules", {"model_name": "synthetic_fleet_eip", "tables": ["nope"]})
    assert ok.structured_content["tables_checked"] == ["gold_fleet.f_booking"]
    assert bad.is_error and "nope" in bad.content[0].text


@pytest.mark.anyio
async def test_kb_tools(server):
    async with Client(server) as c:
        listing = await c.call_tool("list_kb", {})
        doc = await c.call_tool("read_kb", {"name": "glossary.md"})
        escape = await c.call_tool("read_kb", {"name": "../conftest.py"})
        entry = await c.call_tool("get_report_map", {"report_id": "22222222-2222-2222-2222-222222222222"})
    assert [f["name"] for f in listing.structured_content["files"]] == ["glossary.md", "report_map.yaml"]
    assert "Grain" in doc.structured_content["content"] and escape.is_error
    assert entry.structured_content["tables"]["Booking"]["table"] == "gold_fleet.f_booking"


@pytest.mark.anyio
async def test_run_rules_usage_from_report(server, monkeypatch):
    from dma import tools as tools_mod
    from dma.connectors.powerbi import PbiColumn, PbiModelMetadata

    cols = [PbiColumn(table="Booking", column=c) for c in ("booking_id", "driver_id", "booking_amount", "driver_nm", "data_area_party_id")] + [PbiColumn(table="Calendar", column="date")]
    md = PbiModelMetadata(report_id="r", dataset_id="d", workspace_id=None, tables=["Booking", "Calendar"], columns=cols, measures=None, measures_status="x", m_transforms=None, m_transforms_status="x", truncated=False, routes_tried=[])
    monkeypatch.setattr(tools_mod, "model_metadata", lambda client, report_id, workspace_id: md)
    report = "22222222-2222-2222-2222-222222222222"
    async with Client(server) as c:
        r = await c.call_tool("run_rules", {"model_name": "synthetic_fleet_eip", "tables": ["f_booking"], "report_id": report})
        both = await c.call_tool("run_rules", {"model_name": "synthetic_fleet_eip", "report_id": report, "column_usage": []})
        unmapped = await c.call_tool("run_rules", {"model_name": "synthetic_fleet_eip", "report_id": "33333333-3333-3333-3333-333333333333"})
    data = r.structured_content
    assert [f["column"] for f in data["findings"] if f["rule_id"] == "GOLD-001"] == ["booking_date"]
    assert "Calendar" in data["notes"][1]
    assert both.is_error and unmapped.is_error and "not in" in unmapped.content[0].text


@pytest.mark.anyio
async def test_lookup_and_lineage_tools(server):
    async with Client(server) as c:
        models = await c.call_tool("list_models", {})
        bronze = await c.call_tool("find_tables_by_bronze", {"bronze_table": "brnz_fleetops.booking"})
        grain = await c.call_tool("find_tables_by_grain", {"grain_text": "vehicle_id"})
        dbt = await c.call_tool("dbt_upstream", {"model_name": "fct_booking"})
        sql = await c.call_tool("sql_column_lineage", {"sql": "select sum(b.amount) as total from brnz_fleetops.booking b", "column": "total", "dialect": "redshift"})
    assert len(models.structured_content["models"]) == 2
    assert "gold_fleet.f_booking" in {t["table"] for t in bronze.structured_content["tables"]}
    assert "gold_logistics.d_vehicle" in {t["table"] for t in grain.structured_content["tables"]}
    assert [b["table"] for b in dbt.structured_content["bronze_tables"]] == ["brnz_fleetops.booking", "brnz_fleetops.meter_change"]
    assert sql.structured_content["sources"][0]["table"] == "brnz_fleetops.booking"


async def approved_session(c, flow="lineage"):
    started = (await c.call_tool("start_session", {"flow": flow, "scope": "synthetic_fleet_eip f_booking"})).structured_content
    sid = started["session_id"]
    for st in started["stages"]:
        v = (await c.call_tool("submit_stage", {"session_id": sid, "stage": st["stage"], "content": f"APPROVED {st['stage']}"})).structured_content
        r = await c.call_tool("record_decision", {"session_id": sid, "stage": st["stage"], "version": v["version"], "decision": "approve"})
        assert not r.is_error, r.content
    return sid


@pytest.mark.anyio
async def test_write_artifact_refuses_escape_and_writes_inside(server, tmp_path):
    async with Client(server) as c:
        sid = await approved_session(c)
        bad = await c.call_tool("write_artifact", {"kind": "findings_md", "name": "../escape", "content": "x", "session_id": sid})
        good = await c.call_tool("write_artifact", {"kind": "findings_md", "name": "review", "content": "# Findings\n", "session_id": sid})
        view = (await c.call_tool("get_session", {"session_id": sid})).structured_content
    assert bad.is_error
    assert not (tmp_path / "escape.md").exists()
    written = list((tmp_path / "output").glob("*_review.md"))
    assert len(written) == 1 and good.structured_content["path"] == str(written[0])
    assert view["artifacts"] == [str(written[0])]


@pytest.mark.anyio
async def test_write_artifact_blocked_until_final_approval(server, tmp_path):
    args = {"kind": "findings_md", "name": "early", "content": "x"}
    async with Client(server) as c:
        missing = await c.call_tool("write_artifact", args)
        unknown = await c.call_tool("write_artifact", {**args, "session_id": "0" * 32})
        sid = (await c.call_tool("start_session", {"flow": "review", "scope": "model X"})).structured_content["session_id"]
        fresh = await c.call_tool("write_artifact", {**args, "session_id": sid})
        await c.call_tool("submit_stage", {"session_id": sid, "stage": "scope", "content": "scope"})
        await c.call_tool("record_decision", {"session_id": sid, "stage": "scope", "version": 1, "decision": "approve"})
        partial = await c.call_tool("write_artifact", {**args, "session_id": sid})
        report = await c.call_tool("compile_report", {"session_id": sid})
    assert missing.is_error and unknown.is_error and fresh.is_error and report.is_error
    assert partial.is_error and "blocked" in partial.content[0].text and "'findings'" in partial.content[0].text
    assert not list((tmp_path / "output").glob("*.md"))


@pytest.mark.anyio
async def test_record_decision_refuses_inferred_words(server):
    async with Client(server) as c:
        sid = (await c.call_tool("start_session", {"flow": "review", "scope": "model X"})).structured_content["session_id"]
        await c.call_tool("submit_stage", {"session_id": sid, "stage": "scope", "content": "scope"})
        vague = await c.call_tool("record_decision", {"session_id": sid, "stage": "scope", "version": 1, "decision": "looks fine"})
        skip = await c.call_tool("submit_stage", {"session_id": sid, "stage": "intake", "content": "x"})
    assert vague.is_error and skip.is_error


@pytest.mark.anyio
async def test_compile_report_uses_approved_content_only(server, tmp_path):
    async with Client(server) as c:
        sid = (await c.call_tool("start_session", {"flow": "lineage", "scope": "f_booking"})).structured_content["session_id"]
        await c.call_tool("submit_stage", {"session_id": sid, "stage": "scope", "content": "SUPERSEDED scope"})
        await c.call_tool("record_decision", {"session_id": sid, "stage": "scope", "version": 1, "decision": "suggest_changes", "note": "add the | pipe column"})
        await c.call_tool("submit_stage", {"session_id": sid, "stage": "scope", "content": "APPROVED scope"})
        await c.call_tool("record_decision", {"session_id": sid, "stage": "scope", "version": 2, "decision": "approve"})
        await c.call_tool("submit_stage", {"session_id": sid, "stage": "bronze_dependencies", "content": "DISCARDED bronze"})
        await c.call_tool("record_decision", {"session_id": sid, "stage": "bronze_dependencies", "version": 1, "decision": "disapprove", "note": "wrong manifest", "then": "redo"})
        for stage in ("bronze_dependencies", "column_lineage", "diagram"):
            v = (await c.call_tool("submit_stage", {"session_id": sid, "stage": stage, "content": f"APPROVED {stage}"})).structured_content["version"]
            await c.call_tool("record_decision", {"session_id": sid, "stage": stage, "version": v, "decision": "approve"})
        r = await c.call_tool("compile_report", {"session_id": sid})
    assert not r.is_error, r.content
    paths = [a["path"] for a in r.structured_content["artifacts"]]
    md = open(paths[0], encoding="utf-8").read()
    data = json.loads(open(paths[1], encoding="utf-8").read())
    assert "SUPERSEDED" not in md and "DISCARDED" not in md
    assert all(f"APPROVED {s}" in md for s in ("scope", "bronze_dependencies", "column_lineage", "diagram"))
    assert "wrong manifest" in md and "add the \\| pipe column" in md
    assert [s["content"] for s in data["approved_stages"]] == ["APPROVED scope", "APPROVED bronze_dependencies", "APPROVED column_lineage", "APPROVED diagram"]
    assert [e["decision"] for e in data["decision_log"]] == ["suggest_changes", "approve", "disapprove", "approve", "approve", "approve"]
    assert "SUPERSEDED" not in json.dumps(data["approved_stages"])
