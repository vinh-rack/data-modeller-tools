"""Spawn dma-server over stdio, list tools, call run_rules on a fixture model. Exit 1 on any failure.

Run: uv run python scripts/smoke_client.py [model_name]
"""

import asyncio
import os
import shutil
import sys

from mcp import Client, StdioServerParameters

DEFAULT_MODEL = "synthetic_fleet_eip"


async def main(model: str) -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("uv not found on PATH", file=sys.stderr)
        return 1
    env = {**os.environ, "DMA_SMX_DIR": os.environ.get("DMA_SMX_DIR", "tests/fixtures/smx"), "DMA_DBT_MANIFEST": os.environ.get("DMA_DBT_MANIFEST", "tests/fixtures/dbt/manifest.json")}
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
        return 0 if data["findings"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL)))
