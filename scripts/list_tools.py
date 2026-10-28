"""Spawn dma-server over stdio and list its tools as bullets: name - description. Calls no tools.

Run: uv run python scripts/list_tools.py
"""

import asyncio
import shutil
import sys

from mcp import Client, StdioServerParameters


async def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("uv not found on PATH", file=sys.stderr)
        return 1
    async with Client(StdioServerParameters(command=uv, args=["run", "--quiet", "dma-server"])) as client:
        tools = (await client.list_tools()).tools
    print(f"{len(tools)} tools\n")
    for t in tools:
        print(f"- {t.name} - {t.description}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
