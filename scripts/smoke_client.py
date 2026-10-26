"""Spawn dma-server over stdio, list tools, call run_rules on a fixture model, then run one gated session to approval. Exit 1 on any failure.

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


async def gate_check(client: Client) -> bool:
    started = await client.call_tool("start_session", {"flow": "lineage", "scope": "smoke test"})
    sid = started.structured_content["session_id"]
    early = await client.call_tool("write_artifact", {"kind": "lineage_mermaid", "name": "early", "content": "flowchart LR", "session_id": sid})
    if not early.is_error:
        print("gate: write_artifact was not blocked before approval", file=sys.stderr)
        return False
    for st in started.structured_content["stages"]:
        v = await client.call_tool("submit_stage", {"session_id": sid, "stage": st["stage"], "content": f"smoke {st['stage']}"})
        d = await client.call_tool("record_decision", {"session_id": sid, "stage": st["stage"], "version": v.structured_content["version"], "decision": "approve"})
        if d.is_error:
            print(f"gate: record_decision failed: {d.content[0].text}", file=sys.stderr)
            return False
    report = await client.call_tool("compile_report", {"session_id": sid})
    if report.is_error:
        print(f"gate: compile_report failed: {report.content[0].text}", file=sys.stderr)
        return False
    print(f"gate: write blocked before approval, {len(started.structured_content['stages'])} stages approved, compile_report wrote {len(report.structured_content['artifacts'])} files")
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
