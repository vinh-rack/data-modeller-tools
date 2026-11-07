"""In-process Streamable HTTP for tests: the ASGI app behind httpx2.ASGITransport, no port and no network."""

from contextlib import asynccontextmanager

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

BASE = "http://127.0.0.1:8000"


@asynccontextmanager
async def http_client(app, headers: dict | None = None):
    # ASGITransport does not run the lifespan, and the MCP session manager starts there, so run it here.
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE, headers=headers or {}) as hc:
            yield hc


@asynccontextmanager
async def mcp_client(app, headers: dict | None = None, path: str = "/mcp"):
    async with http_client(app, headers) as hc:
        async with Client(streamable_http_client(f"{BASE}{path}", http_client=hc)) as c:
            yield c
