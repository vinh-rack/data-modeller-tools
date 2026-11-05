"""Spawn dma-server over stdio, list tools, call run_rules on a fixture model, then run one 11-stage build session to approval. Exit 1 on any failure.

Run: uv run python scripts/smoke_client.py [model_name]
Session logs and artifacts from the gate check go to a temp folder, not output/.
"""

import asyncio
import os
import shutil
import sys
import tempfile

import openpyxl
from mcp import Client, StdioServerParameters

DEFAULT_MODEL = "synthetic_fleet_eip"


BRONZE = ["brnz_fleetops.booking", "brnz_fleetops.driver"]
# One missing table per route: store_targets with pasted SQL, refunds by data analysis.
TABLE_STATUS = {"booking": "dbt_model_found", "driver": "already_modelled", "store_targets": "missing", "refunds": "missing"}
TARGETS_SQL = "select s.store_id, s.monthly_target from raw.store_targets_extract s"
CITES = " Evidence: store_targets (route: provided); refunds (route: data_analysis) [inferred]."
WAVE3 = ["Bronze", "Silver - Staging", "Silver - IDM", "GOLD - SDM", "Lookup", "Report Table Mapping"]
# Synthetic rows for the output workbook, by the stage that proposes them.
SMX_ROWS = {
    "bronze_identification": {"Bronze": [{"schema": "brnz_fleetops", "table name": "booking", "source system": "fleetops"}, {"schema": "brnz_fleetops", "table name": "driver", "source system": "fleetops"}]},
    "silver_staging": {"Silver - Staging": [{"target schema": "slvr_stg", "target table": "booking", "target column name": "booking_num", "source table name": "brnz_fleetops.booking", "source column name": "bookingno", "join/rule/filter applied": "pk"}]},
    "silver_idm": {"Silver - IDM": [{"schema": "slvr_cdm", "table_name": "booking", "column name": "booking_id", "source table / file": "slvr_stg.booking", "source column": "booking_num", "pk": "Y", "rules": "hash(booking_num)"}]},
    "gold": {
        "GOLD - SDM": [{"schema name": "gold_fleet", "table name": "f_booking", "column name": "booking_id", "pk": "y", "source table": "slvr_cdm.booking", "source column": "booking_id", "rules": "1:1 mapping"}],
        "Report Table Mapping": [{"PBI Report": "Synthetic Fleet Bookings", "Tables": "gold_fleet.f_booking", "SMX": "synthetic_fleet_eip", "PBI Table": "Booking"}],
    },
}


def fail(msg: str) -> bool:
    print(f"gate: {msg}", file=sys.stderr)
    return False


async def gate_check(client: Client) -> bool:
    """The build flow end to end on fixtures: 11 gates, the permission declined, then the three final outputs."""
    started = (await client.call_tool("start_session", {"flow": "build", "scope": "smoke test report"})).structured_content
    sid = started["session_id"]
    majors = [s["major_gate"] for s in started["stages"] if s.get("major_gate")]
    if len(started["stages"]) != 11 or majors != ["Bronze table list", "Silver schemas", "Gold models"]:
        return fail(f"unexpected build stages or major gates: {majors}")
    early = await client.call_tool("write_artifact", {"kind": "lineage_mermaid", "name": "early", "content": "flowchart LR", "session_id": sid})
    if not early.is_error:
        return fail("write_artifact was not blocked before approval")
    refused = None
    for st in started["stages"]:
        stage = st["stage"]
        args = {"session_id": sid, "stage": stage, "content": f"smoke {stage}." + (CITES if stage in ("reuse_check", "silver_staging", "silver_idm", "gold") else "")}
        if stage == "bronze_identification":
            args["bronze_tables"] = BRONZE
            args["table_status"] = TABLE_STATUS
        if stage in SMX_ROWS:
            args["smx_rows"] = SMX_ROWS[stage]
        if stage == "data_extraction":
            # The modeller declined, so no grant is logged and sampling must be refused.
            refused = await client.call_tool("sample_rows", {"session_id": sid, "table": BRONZE[0]})
            # The modeller chose a route per missing table after major gate 1.
            for call, a in [("choose_missing_route", {"table": "store_targets", "route": "provided_code"}), ("provide_table_code", {"table": "store_targets", "code": TARGETS_SQL, "dialect": "databricks"}), ("choose_missing_route", {"table": "refunds", "route": "data_analysis"})]:
                r = await client.call_tool(call, {"session_id": sid, **a})
                if r.is_error:
                    return fail(f"{call} failed: {r.content[0].text}")
            args["content"] = "skipped: the modeller declined data extraction, so data analysis is metadata only"
        v = await client.call_tool("submit_stage", args)
        if v.is_error:
            return fail(f"submit_stage {stage} failed: {v.content[0].text}")
        d = await client.call_tool("record_decision", {"session_id": sid, "stage": stage, "version": v.structured_content["version"], "decision": "approve"})
        if d.is_error:
            return fail(f"record_decision failed: {d.content[0].text}")
    if refused is None or not refused.is_error:
        return fail("sample_rows was not refused without a permission")
    report = await client.call_tool("compile_report", {"session_id": sid})
    if report.is_error:
        return fail(f"compile_report failed: {report.content[0].text}")
    mermaid = (await client.call_tool("lineage_diagram", {"table": "gold_fleet.f_booking"})).structured_content["mermaid"]
    writes = [
        {"kind": "proposed_smx_xlsx", "name": "smoke_model", "content": ""},
        {"kind": "requirements_md", "name": "smoke_requirements", "content": ""},
        {"kind": "lineage_mermaid", "name": "smoke_lineage", "content": mermaid},
    ]
    for w in writes:
        r = await client.call_tool("write_artifact", {**w, "session_id": sid})
        if r.is_error:
            return fail(f"write_artifact {w['kind']} failed: {r.content[0].text}")
        if w["kind"] == "proposed_smx_xlsx":
            wb = openpyxl.load_workbook(r.structured_content["path"], read_only=True)
            names, gold = wb.sheetnames, [row for row in wb["GOLD - SDM"].iter_rows(min_row=2, values_only=True)]
            wb.close()
            if names != WAVE3 or len(gold) != 1:
                return fail(f"unexpected workbook: sheets {names}, {len(gold)} Gold rows")
    print(f"gate: write blocked before approval, {len(started['stages'])} build stages approved, sampling refused without permission, one missing table per route, compile_report wrote {len(report.structured_content['artifacts'])} files, then the 6-sheet workbook, requirements and lineage")
    return True


async def main(model: str) -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("uv not found on PATH", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "DMA_SMX_DIR": os.environ.get("DMA_SMX_DIR", "tests/fixtures/smx"),
            "DMA_DBT_MANIFEST": os.environ.get("DMA_DBT_MANIFEST", "tests/fixtures/dbt/manifest.json"),
            "DMA_OUTPUT_DIR": tmp,
        }
        params = StdioServerParameters(command=uv, args=["run", "--quiet", "dma-server"], env=env)
        async with Client(params) as client:
            tools = [t.name for t in (await client.list_tools()).tools]
            print(f"tools ({len(tools)}): {', '.join(tools)}")
            result = await client.call_tool("run_rules", {"model_name": model})
            if result.is_error:
                print(f"run_rules failed: {result.content[0].text}", file=sys.stderr)
                return 1
            data = result.structured_content
            print(f"run_rules {model}: {len(data['tables_checked'])} tables, counts {data['counts']}")
            for f in data["findings"]:
                print(f"  {f['severity']:<7} {f['rule_id']:<9} {f['table']} {f['column'] or ''} : {f['message']}")
            if not data["findings"]:
                return 1
            return 0 if await gate_check(client) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL)))
