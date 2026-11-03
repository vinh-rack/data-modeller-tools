"""Spawn dma-server over stdio, list tools, call run_rules on a fixture model, then run one 11-stage build session to approval. Exit 1 on any failure.

Run: uv run python scripts/smoke_client.py [model_name]
Session logs and artifacts from the gate check go to a temp folder, not output/.
"""

import asyncio
import os
import shutil
import sys
import tempfile

from mcp import Client, StdioServerParameters

DEFAULT_MODEL = "synthetic_fleet_eip"


BRONZE = ["brnz_fleetops.booking", "brnz_fleetops.driver"]


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
        args = {"session_id": sid, "stage": stage, "content": f"smoke {stage}"}
        if stage == "bronze_identification":
            args["bronze_tables"] = BRONZE
        if stage == "data_extraction":
            # The modeller declined, so no grant is logged and sampling must be refused.
            refused = await client.call_tool("sample_rows", {"session_id": sid, "table": BRONZE[0]})
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
    print(f"gate: write blocked before approval, {len(started['stages'])} build stages approved, sampling refused without permission, compile_report wrote {len(report.structured_content['artifacts'])} files, then xlsx, requirements and lineage")
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
