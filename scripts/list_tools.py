"""Spawn dma-server over stdio and list its tools with their read-only hint and description. Calls no tools.

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
    width = max(len(t.name) for t in tools)
    print(f"{len(tools)} tools\n")
    for t in tools:
        mode = "read" if t.annotations and t.annotations.read_only_hint else "write"
        print(f"{t.name:<{width}}  {mode:<5}  {t.description}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
