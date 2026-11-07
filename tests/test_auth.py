"""HTTP authentication: the dev token, startup guards, Protected Resource Metadata and log redaction."""

import logging

import pytest

from conftest import CONFIG_DIR, SMX_DIR
from dma.auth import AuthConfigError, install_redaction, load_auth
from dma.config import load_settings
from dma.server import build_http
from httpkit import http_client, mcp_client

pytestmark = pytest.mark.anyio

DEV_TOKEN = "dev-token-for-tests-only-0123456789abcdef"
MCP_HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def env_for(tmp_path, **extra):
    return {
        "DMA_SMX_DIR": str(SMX_DIR), "DMA_OUTPUT_DIR": str(tmp_path / "output"), "DMA_CONFIG_DIR": str(CONFIG_DIR),
        "DMA_TRANSPORT": "http", "DMA_AUTH": "dev", "DMA_DEV_TOKEN": DEV_TOKEN, "DMA_DEV_USER_ID": "dev-user",
        "DMA_DEV_USER_NAME": "Dev Modeller", "DMA_RESOURCE_SERVER_URL": "http://127.0.0.1:8000/mcp", **extra,
    }


def dev_app(tmp_path, **extra):
    env = env_for(tmp_path, **extra)
    return build_http(load_settings(env), env)


async def test_no_token_gets_401_with_resource_metadata(tmp_path):
    async with http_client(dev_app(tmp_path)) as hc:
        r = await hc.post("/mcp", headers=MCP_HEADERS, json=PING)
    assert r.status_code == 401
    challenge = r.headers["www-authenticate"]
    assert challenge.startswith("Bearer ") and 'resource_metadata="http://127.0.0.1:8000/.well-known/oauth-protected-resource/mcp"' in challenge


async def test_wrong_token_gets_401(tmp_path):
    async with http_client(dev_app(tmp_path)) as hc:
        r = await hc.post("/mcp", headers={**MCP_HEADERS, "authorization": f"Bearer {DEV_TOKEN}x"}, json=PING)
        basic = await hc.post("/mcp", headers={**MCP_HEADERS, "authorization": f"Basic {DEV_TOKEN}"}, json=PING)
    assert r.status_code == 401 and basic.status_code == 401
    assert DEV_TOKEN not in r.text


async def test_protected_resource_metadata_is_served(tmp_path):
    async with http_client(dev_app(tmp_path)) as hc:
        r = await hc.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200
    meta = r.json()
    assert meta["resource"] == "http://127.0.0.1:8000/mcp" and meta["authorization_servers"]


async def test_dev_token_lists_tools_and_names_the_gate_user(tmp_path):
    async with mcp_client(dev_app(tmp_path), headers={"authorization": f"Bearer {DEV_TOKEN}"}) as c:
        tools = (await c.list_tools()).tools
        started = (await c.call_tool("start_session", {"flow": "lineage", "scope": "dev auth test"})).structured_content
    assert len(tools) == 28
    assert started["created_by"] == "Dev Modeller (dev-user)"


async def test_health_stays_open_with_auth_on(tmp_path):
    async with http_client(dev_app(tmp_path)) as hc:
        r = await hc.get("/health")
    assert r.status_code == 200 and set(r.json()) == {"status", "version", "commit"}


@pytest.mark.parametrize("extra,match", [
    ({"DMA_AUTH": ""}, "DMA_AUTH"),
    ({"DMA_AUTH": "none"}, "DMA_AUTH"),
    ({"DMA_DEV_TOKEN": ""}, "DMA_DEV_TOKEN"),
    ({"DMA_DEV_TOKEN": "short"}, "DMA_DEV_TOKEN"),
    ({"DMA_DEV_USER_ID": "../evil"}, "DMA_DEV_USER_ID"),
    ({"DMA_HTTP_HOST": "0.0.0.0"}, "DMA_ALLOW_DEV_AUTH_ON_NETWORK"),
    ({"DMA_HTTP_HOST": "0.0.0.0", "DMA_ALLOW_DEV_AUTH_ON_NETWORK": "yes"}, "DMA_ALLOW_DEV_AUTH_ON_NETWORK"),
])
def test_dev_mode_refuses_to_start(tmp_path, extra, match):
    env = env_for(tmp_path, **extra)
    with pytest.raises(AuthConfigError, match=match):
        load_auth(env, load_settings(env).http)


def test_dev_mode_on_network_needs_the_override_and_warns(tmp_path, caplog):
    env = env_for(tmp_path, DMA_HTTP_HOST="0.0.0.0", DMA_ALLOW_DEV_AUTH_ON_NETWORK="1")
    with caplog.at_level(logging.WARNING, logger="dma.auth"):
        cfg = load_auth(env, load_settings(env).http)
    assert cfg.mode == "dev"
    text = caplog.text
    assert "local testing only" in text and "0.0.0.0" in text
    assert DEV_TOKEN not in text and DEV_TOKEN not in repr(cfg)


def test_dev_mode_on_loopback_still_warns(tmp_path, caplog):
    env = env_for(tmp_path)
    with caplog.at_level(logging.WARNING, logger="dma.auth"):
        load_auth(env, load_settings(env).http)
    assert "local testing only" in caplog.text


def test_redaction_covers_messages_args_and_exceptions(tmp_path, caplog):
    secret = "client-secret-value-0123456789"
    install_redaction({"DMA_DEV_TOKEN": DEV_TOKEN, "DMA_ENTRA_CLIENT_SECRET": secret, "PBI_ACCESS_TOKEN": "", "DMA_DBT_GIT_TOKEN": "short"})
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"
    logger = logging.getLogger("uvicorn.error.test")
    with caplog.at_level(logging.INFO):
        logger.info("header Authorization: Bearer %s", "abc.def-ghi")
        logger.info("dev token %s and secret %s", DEV_TOKEN, secret)
        logger.info(f"raw jwt {jwt}")
        try:
            raise RuntimeError(f"failed with {secret}")
        except RuntimeError:
            logger.exception("boom")
    text = caplog.text + "".join(r.exc_text or "" for r in caplog.records)
    for leaked in (DEV_TOKEN, secret, jwt, "abc.def-ghi"):
        assert leaked not in text
    assert "Bearer [redacted]" in text and "[redacted-jwt]" in text
