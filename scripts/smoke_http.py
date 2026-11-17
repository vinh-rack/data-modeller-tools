"""Start the v2 container with docker compose, wait for /health, connect over Streamable HTTP with a throwaway dev token,
list the tools, then run the same gated 11-stage build session as smoke_client.py, getting every output back through
the client (text inline, the workbook as an MCP resource). Exit 1 on any failure. The container and its volumes are
removed at the end, so nothing is left behind.

Run: uv run python scripts/smoke_http.py [--keep]
Needs Docker. Uses synthetic fixtures only: tests/fixtures/smx, tests/fixtures/kb and tests/fixtures/dbt_repo.
"""

import asyncio
import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from smoke_client import gate_check

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "dma-smoke"
BASE = "http://127.0.0.1:8000"


def compose(*args: str, env: dict, override: Path) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "docker-compose.yml"), "-f", str(override), *args]
    return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)


def wait_healthy(timeout: float = 180) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=3) as r:
                return json.loads(r.read())
        except OSError:
            time.sleep(1)
    raise TimeoutError(f"/health did not answer within {timeout} s")


async def run(token: str) -> bool:
    headers = {"authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=60) as hc:
        anonymous = await hc.post(f"{BASE}/mcp", headers={"authorization": "", "content-type": "application/json", "accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        if anonymous.status_code != 401:
            print(f"auth: expected 401 without a token, got {anonymous.status_code}", file=sys.stderr)
            return False
        async with Client(streamable_http_client(f"{BASE}/mcp", http_client=hc)) as client:
            tools = [t.name for t in (await client.list_tools()).tools]
            print(f"tools ({len(tools)}): {', '.join(tools)}")
            if len(tools) != 28:
                return False

            async def fetch_workbook(written: dict) -> bytes:
                contents = (await client.read_resource(written["uri"])).contents
                return base64.b64decode(contents[0].blob)

            if not await gate_check(client, fetch_workbook=fetch_workbook):
                return False
            sessions = (await client.call_tool("list_sessions", {})).structured_content["sessions"]
            sid = sessions[-1]["session_id"]
            view = (await client.call_tool("get_session", {"session_id": sid})).structured_content
            texts = [p for p in view["artifacts"] if not p.endswith(".xlsx")]
            for path in texts:
                text = (await client.read_resource(f"dma://artifacts/{sid}/{Path(path).name}")).contents[0].text
                if not text.strip():
                    print(f"outputs: empty resource for {Path(path).name}", file=sys.stderr)
                    return False
            owners = {Path(p).parent.name for p in view["artifacts"]}
            print(f"outputs: {len(view['artifacts'])} artifacts, {len(texts)} text files read back as resources, all in output/{', '.join(sorted(owners))}/; gate user {view['created_by']}")
            return owners == {"dev"}


def main() -> int:
    keep = "--keep" in sys.argv
    token = secrets.token_urlsafe(32)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or "unknown"
    env = {**os.environ, "DMA_DEV_TOKEN": token, "DMA_GIT_COMMIT": commit}
    with tempfile.TemporaryDirectory() as tmp:
        # A synthetic dbt repo in place of the clone volume, read as files only, and no pull.
        override = Path(tmp) / "smoke.override.yml"
        dbt = (ROOT / "tests" / "fixtures" / "dbt_repo").as_posix()
        override.write_text(f"services:\n  dma:\n    environment:\n      DMA_DBT_PULL: \"off\"\n    volumes:\n      - \"{dbt}:/data/dbt:ro\"\n", encoding="utf-8")
        up = compose("up", "-d", "--build", "--wait", env=env, override=override)
        try:
            if up.returncode != 0:
                print(f"docker compose up failed:\n{up.stderr[-2000:]}", file=sys.stderr)
                return 1
            health = wait_healthy()
            print(f"health: {health}")
            if set(health) != {"status", "version", "commit"} or health["commit"] != commit:
                return 1
            ok = asyncio.run(run(token))
            logs = compose("logs", "--no-color", env=env, override=override).stdout
            if token in logs:
                print("logs: the dev token appears in the container logs", file=sys.stderr)
                return 1
            print("logs: no token in the container logs")
            return 0 if ok else 1
        finally:
            if not keep:
                compose("down", "-v", env=env, override=override)


if __name__ == "__main__":
    sys.exit(main())
