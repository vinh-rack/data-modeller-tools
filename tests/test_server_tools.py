import json

import pytest
from mcp import Client

from conftest import CONFIG_DIR, FIXTURES, SMX_DIR
from dma.config import load_settings
from dma.server import build_server
from dma.tools import DmaTools

WRITE_TOOLS = {"start_session", "submit_stage", "record_decision", "grant_permission", "revoke_permission", "compile_report", "write_artifact"}
EXPECTED_TOOLS = {"list_models", "get_model", "find_tables_by_bronze", "find_tables_by_grain", "run_rules", "dbt_model_lookup", "dbt_upstream", "dbt_repo_status", "sql_column_lineage", "get_session", "list_sessions", "list_kb", "read_kb", "get_report_map", "lineage_diagram", "report_lineage", "sample_rows", "pbi_get_report", "pbi_model_metadata"} | WRITE_TOOLS


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
async def test_report_lineage_tool(server, monkeypatch):
    from dma import tools as tools_mod
    from dma.connectors.powerbi import PbiModelMetadata

    md = PbiModelMetadata(report_id="r", dataset_id="d", workspace_id=None, tables=["Booking", "Calendar"], columns=[], measures=None, measures_status="x", m_transforms=None, m_transforms_status="x", truncated=False, routes_tried=[])
    monkeypatch.setattr(tools_mod, "model_metadata", lambda client, report_id, workspace_id: md)
    async with Client(server) as c:
        r = await c.call_tool("report_lineage", {"report_id": "22222222-2222-2222-2222-222222222222"})
    rows = {x["pbi_table"]: x for x in r.structured_content["tables"]}
    assert rows["Booking"]["status"] == "confirmed" and rows["Calendar"]["status"] == "unknown"
    assert rows["Driver"]["note"] == "in the report map but not in the Power BI model"


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


@pytest.fixture
def dbt_server(tmp_path):
    settings = load_settings({
        "DMA_SMX_DIR": str(SMX_DIR),
        "DMA_OUTPUT_DIR": str(tmp_path / "output"),
        "DMA_CONFIG_DIR": str(CONFIG_DIR),
        "DMA_DBT_REPO": str(FIXTURES / "dbt_repo"),
        # The manifest is set too, to show the repo reader wins. No git runs in tests.
        "DMA_DBT_MANIFEST": str(FIXTURES / "dbt" / "manifest.json"),
        "DMA_DBT_PULL": "off",
    })
    return build_server(DmaTools(settings, pbi_factory=object))


@pytest.mark.anyio
async def test_dbt_model_lookup_found_and_missing(dbt_server):
    async with Client(dbt_server) as c:
        found = (await c.call_tool("dbt_model_lookup", {"name": "stg_c_clean"})).structured_content
        missing = (await c.call_tool("dbt_model_lookup", {"name": "stg_zzz"})).structured_content
    m = found["models"][0]
    assert found["exists"] and found["route"] == "dbt_model" and (m["name"], m["match"], m["alias"]) == ("stg_c", "alias", "stg_c_clean")
    assert m["path"] == "models/staging/stg_c.sql" and "{{" in m["raw_sql"] and "{{" not in m["rendered_sql"] and m["parses"]
    assert [s["table"] for s in m["sources"]] == ["raw_c"] and [u["kind"] for u in m["unresolved"]] == ["jinja_expression", "incremental_block"]
    assert (missing["exists"], missing["route"], missing["models"]) == (False, "data_analysis", []) and "data analysis" in missing["note"]


@pytest.mark.anyio
async def test_dbt_upstream_uses_repo_over_manifest(dbt_server):
    async with Client(dbt_server) as c:
        up = (await c.call_tool("dbt_upstream", {"model_name": "mart_ab"})).structured_content
    assert up["reader"] == "repo" and [b["table"] for b in up["bronze_tables"]] == ["legacy.old_x", "raw.raw_a", "raw.raw_b"]


@pytest.mark.anyio
async def test_dbt_upstream_manifest_fallback_and_status(server, dbt_server):
    async with Client(server) as c:
        fallback = (await c.call_tool("dbt_upstream", {"model_name": "fct_booking"})).structured_content
        unset = (await c.call_tool("dbt_repo_status", {})).structured_content
        lookup = await c.call_tool("dbt_model_lookup", {"name": "x"})
    async with Client(dbt_server) as c:
        status = (await c.call_tool("dbt_repo_status", {})).structured_content
    assert fallback["reader"] == "manifest" and unset["configured"] is False and lookup.is_error and "DMA_DBT_REPO" in lookup.content[0].text
    assert (status["project"], status["models"], status["sources"], status["seeds"]) == ("shop_pkg", 9, 4, 1)
    assert status["this_session_pull"]["status"] == "skipped_off" and status["pull_mode"] == "off"


class FakeSampler:
    def __init__(self):
        self.calls = []

    def sample(self, table, limit):
        self.calls.append((table, limit))
        return [{"id": i} for i in range(50)]


async def build_to_extraction(c, bronze=("raw.raw_orders", "raw.raw_customers")):
    started = (await c.call_tool("start_session", {"flow": "build", "scope": "report R"})).structured_content
    sid = started["session_id"]
    for st in [s["stage"] for s in started["stages"]][:5]:
        args = {"session_id": sid, "stage": st, "content": f"APPROVED {st}"}
        if st == "bronze_identification":
            args["bronze_tables"] = list(bronze)
        v = (await c.call_tool("submit_stage", args)).structured_content["version"]
        await c.call_tool("record_decision", {"session_id": sid, "stage": st, "version": v, "decision": "approve"})
    return started, sid


@pytest.mark.anyio
async def test_build_session_shows_major_gates(server):
    async with Client(server) as c:
        started, _ = await build_to_extraction(c)
    assert len(started["stages"]) == 11
    assert [(s["major_gate_number"], s["major_gate"]) for s in started["stages"] if s["major_gate"]] == [(1, "Bronze table list"), (2, "Silver schemas"), (3, "Gold models")]


@pytest.mark.anyio
async def test_sample_rows_needs_permission_and_is_a_stub(server):
    async with Client(server) as c:
        _, sid = await build_to_extraction(c)
        before = await c.call_tool("sample_rows", {"session_id": sid, "table": "raw.raw_orders"})
        bad_grant = await c.call_tool("grant_permission", {"session_id": sid, "permission": "data_extraction", "tables": ["raw.raw_payments"]})
        grant = await c.call_tool("grant_permission", {"session_id": sid, "permission": "data_extraction", "tables": ["raw.raw_orders"]})
        stub = (await c.call_tool("sample_rows", {"session_id": sid, "table": "raw.raw_orders", "limit": 500})).structured_content
        other = await c.call_tool("sample_rows", {"session_id": sid, "table": "raw.raw_customers"})
        await c.call_tool("revoke_permission", {"session_id": sid, "permission": "data_extraction"})
        after = await c.call_tool("sample_rows", {"session_id": sid, "table": "raw.raw_orders"})
        view = (await c.call_tool("get_session", {"session_id": sid})).structured_content
    assert before.is_error and "no logged" in before.content[0].text
    assert bad_grant.is_error and not grant.is_error
    assert (stub["status"], stub["limit"], stub["rows"]) == ("not_available", 20, []) and "Phase 0" in stub["reason"]
    assert other.is_error and after.is_error
    assert [p["event"] for p in view["permissions"]] == ["granted", "revoked"]


@pytest.mark.anyio
async def test_sample_rows_caps_rows_with_a_connector(tmp_path):
    settings = load_settings({"DMA_SMX_DIR": str(SMX_DIR), "DMA_OUTPUT_DIR": str(tmp_path / "output"), "DMA_CONFIG_DIR": str(CONFIG_DIR)})
    sampler = FakeSampler()
    srv = build_server(DmaTools(settings, pbi_factory=object, sampler=sampler))
    async with Client(srv) as c:
        _, sid = await build_to_extraction(c)
        await c.call_tool("grant_permission", {"session_id": sid, "permission": "data_extraction", "tables": ["RAW.RAW_ORDERS"]})
        r = (await c.call_tool("sample_rows", {"session_id": sid, "table": "raw.raw_orders", "limit": 99})).structured_content
    assert sampler.calls == [("raw.raw_orders", 20)] and r["status"] == "ok" and len(r["rows"]) == 20
    files = [p for p in (tmp_path / "output").rglob("*") if p.is_file()]
    assert [p.name for p in files] == ["events.jsonl"] and '"rows"' not in files[0].read_text(encoding="utf-8")


REQUIREMENTS_TEXT = "## Report purpose\n[confirmed] Order overview.\n## Measures\n[unknown] DAX not readable."


async def finish_build(c, sid, requirements=REQUIREMENTS_TEXT):
    view = (await c.call_tool("get_session", {"session_id": sid})).structured_content
    for st in view["stages"]:
        if st["status"] == "approved":
            continue
        content = requirements if st["stage"] == "requirements" else f"APPROVED {st['stage']}"
        v = (await c.call_tool("submit_stage", {"session_id": sid, "stage": st["stage"], "content": content})).structured_content["version"]
        await c.call_tool("record_decision", {"session_id": sid, "stage": st["stage"], "version": v, "decision": "approve"})


@pytest.mark.anyio
async def test_requirements_md_from_approved_version_only(server, tmp_path):
    async with Client(server) as c:
        started = (await c.call_tool("start_session", {"flow": "build", "scope": "report R"})).structured_content
        sid = started["session_id"]
        early = await c.call_tool("write_artifact", {"kind": "requirements_md", "name": "req", "content": "", "session_id": sid})
        for st in ("scope", "powerbi_intake"):
            v = (await c.call_tool("submit_stage", {"session_id": sid, "stage": st, "content": st})).structured_content["version"]
            await c.call_tool("record_decision", {"session_id": sid, "stage": st, "version": v, "decision": "approve"})
        await c.call_tool("submit_stage", {"session_id": sid, "stage": "requirements", "content": "DRAFT purpose guessed"})
        await c.call_tool("record_decision", {"session_id": sid, "stage": "requirements", "version": 1, "decision": "suggest_changes", "note": "no guessing"})
        await finish_build(c, sid)
        with_content = await c.call_tool("write_artifact", {"kind": "requirements_md", "name": "req", "content": "made up", "session_id": sid})
        ok = (await c.call_tool("write_artifact", {"kind": "requirements_md", "name": "req", "content": "", "session_id": sid})).structured_content
    assert early.is_error and "blocked" in early.content[0].text
    assert with_content.is_error and "empty content" in with_content.content[0].text
    text = open(ok["path"], encoding="utf-8").read()
    assert REQUIREMENTS_TEXT in text and "DRAFT" not in text and "version 2" in text
    assert started["stages"][2]["headings"][0] == "Report purpose"
    assert "Pages and visuals" in ok["notes"][0] and "Report purpose" not in ok["notes"][0]


@pytest.mark.anyio
async def test_requirements_md_needs_a_requirements_stage(server):
    async with Client(server) as c:
        sid = await approved_session(c, "lineage")
        r = await c.call_tool("write_artifact", {"kind": "requirements_md", "name": "req", "content": "", "session_id": sid})
    assert r.is_error and "no 'requirements' stage" in r.content[0].text
