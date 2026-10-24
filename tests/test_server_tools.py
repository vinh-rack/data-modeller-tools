import pytest
from mcp import Client

from conftest import CONFIG_DIR, FIXTURES, SMX_DIR
from dma.config import load_settings
from dma.server import build_server
from dma.tools import DmaTools

EXPECTED_TOOLS = {"list_models", "get_model", "find_tables_by_bronze", "find_tables_by_grain", "run_rules", "dbt_upstream", "sql_column_lineage", "write_artifact"}


@pytest.fixture
def server(tmp_path):
    settings = load_settings({
        "DMA_SMX_DIR": str(SMX_DIR),
        "DMA_OUTPUT_DIR": str(tmp_path / "output"),
        "DMA_CONFIG_DIR": str(CONFIG_DIR),
        "DMA_DBT_MANIFEST": str(FIXTURES / "dbt" / "manifest.json"),
    })
    return build_server(DmaTools(settings))


@pytest.mark.anyio
async def test_lists_tools_with_read_only_hints(server):
    async with Client(server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    for name, t in tools.items():
        assert t.description
        assert t.annotations.read_only_hint is (name != "write_artifact")


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


@pytest.mark.anyio
async def test_write_artifact_refuses_escape_and_writes_inside(server, tmp_path):
    async with Client(server) as c:
        bad = await c.call_tool("write_artifact", {"kind": "findings_md", "name": "../escape", "content": "x"})
        good = await c.call_tool("write_artifact", {"kind": "findings_md", "name": "review", "content": "# Findings\n"})
    assert bad.is_error
    assert not (tmp_path / "escape.md").exists()
    written = list((tmp_path / "output").glob("*_review.md"))
    assert len(written) == 1 and good.structured_content["path"] == str(written[0])
