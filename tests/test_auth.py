"""HTTP authentication: the dev token, startup guards, Protected Resource Metadata and log redaction."""

import logging

import pytest

import entrakit as ek
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


# Entra mode, with a local JWKS and signed test tokens only.


def entra_app(tmp_path, jwks=None, **extra):
    env = ek.entra_env(tmp_path, SMX_DIR, CONFIG_DIR, **extra)
    return build_http(load_settings(env), env, jwks_client=jwks or ek.LocalJWKS())


async def status_for(app, tok):
    headers = {**MCP_HEADERS, **({"authorization": f"Bearer {tok}"} if tok else {})}
    async with http_client(app) as hc:
        r = await hc.post("/mcp", headers=headers, json=PING)
    return r


async def test_entra_valid_token_lists_tools_and_names_the_user(tmp_path):
    headers = {"authorization": f"Bearer {ek.token()}"}
    async with mcp_client(entra_app(tmp_path), headers=headers) as c:
        tools = (await c.list_tools()).tools
        started = (await c.call_tool("start_session", {"flow": "lineage", "scope": "entra test"})).structured_content
    assert len(tools) == 28
    assert started["created_by"] == f"User A ({ek.USER_A})"


async def test_entra_audience_may_be_the_client_id_or_the_app_id_uri(tmp_path):
    assert (await status_for(entra_app(tmp_path), ek.token(aud=ek.CLIENT_ID))).status_code == 200
    assert (await status_for(entra_app(tmp_path), ek.token(aud=ek.AUDIENCE))).status_code == 200


@pytest.mark.parametrize("case,make", [
    ("no token", lambda: None),
    ("not a jwt", lambda: "not-a-jwt"),
    ("bad signature", lambda: ek.token(key=ek.OTHER_KEY)),
    ("unknown kid", lambda: ek.token(kid="other-kid")),
    ("wrong audience", lambda: ek.token(aud="api://someone-else")),
    ("graph audience", lambda: ek.token(aud="https://graph.microsoft.com")),
    ("wrong issuer", lambda: ek.token(iss="https://login.microsoftonline.com/99999999-9999-4999-8999-999999999999/v2.0")),
    ("v1 issuer", lambda: ek.token(iss=f"https://sts.windows.net/{ek.TENANT}/")),
    ("tid mismatch", lambda: ek.token(tid="99999999-9999-4999-8999-999999999999")),
    ("expired", lambda: ek.token(exp=1_000_000, iat=999_000, nbf=999_000)),
    ("not yet valid", lambda: ek.token(nbf=4_000_000_000)),
    ("no oid", lambda: ek.token(oid=None)),
    ("hs256", lambda: jwt_hs256()),
])
async def test_entra_refuses_bad_tokens_with_401(tmp_path, case, make):
    r = await status_for(entra_app(tmp_path), make())
    assert r.status_code == 401, case
    assert "resource_metadata=" in r.headers.get("www-authenticate", "")


def jwt_hs256():
    import jwt as pyjwt

    return pyjwt.encode(ek.claims(), "shared-secret-shared-secret-shared-secret", algorithm="HS256", headers={"kid": ek.KID})


@pytest.mark.parametrize("case,over", [
    ("other scope", {"scp": "user_impersonation"}),
    ("app-only token", {"scp": None, "roles": ["dma.read"]}),
])
async def test_entra_missing_scope_gets_403(tmp_path, case, over):
    r = await status_for(entra_app(tmp_path), ek.token(**over))
    assert r.status_code == 403, case
    assert 'error="insufficient_scope"' in r.headers["www-authenticate"]


async def test_entra_allowlist(tmp_path):
    by_user = entra_app(tmp_path, DMA_ALLOWED_USERS=ek.USER_A)
    assert (await status_for(by_user, ek.token())).status_code == 200
    assert (await status_for(entra_app(tmp_path, DMA_ALLOWED_USERS=ek.USER_A), ek.token(oid=ek.USER_B, name="User B"))).status_code == 403
    by_group = entra_app(tmp_path, DMA_ALLOWED_GROUPS=ek.GROUP)
    assert (await status_for(by_group, ek.token(oid=ek.USER_B, groups=[ek.GROUP]))).status_code == 200
    # Matching is on oid only: a name or upn that looks like an allowed id does not count.
    lookalike = ek.token(oid=ek.USER_B, name=ek.USER_A, preferred_username=ek.USER_A)
    assert (await status_for(entra_app(tmp_path, DMA_ALLOWED_USERS=ek.USER_A), lookalike)).status_code == 403


async def test_entra_group_overage_is_refused(tmp_path, caplog):
    overage = ek.token(oid=ek.USER_B, _claim_names={"groups": "src1"}, _claim_sources={"src1": {"endpoint": "https://graph.example.test"}})
    with caplog.at_level(logging.WARNING, logger="dma.auth"):
        r = await status_for(entra_app(tmp_path, DMA_ALLOWED_GROUPS=ek.GROUP), overage)
    assert r.status_code == 403 and "overage" in caplog.text


async def test_entra_metadata_advertises_full_scopes_and_the_tenant(tmp_path):
    async with http_client(entra_app(tmp_path)) as hc:
        meta = (await hc.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert meta["resource"] == "http://127.0.0.1:8000/mcp"
    assert meta["authorization_servers"] == [ek.ISSUER]
    assert meta["scopes_supported"] == [f"{ek.AUDIENCE}/{ek.SCOPE}"]


async def test_entra_keys_are_cached(tmp_path):
    jwks = ek.LocalJWKS()
    async with http_client(entra_app(tmp_path, jwks=jwks)) as hc:
        for _ in range(3):
            r = await hc.post("/mcp", headers={**MCP_HEADERS, "authorization": f"Bearer {ek.token()}"}, json=PING)
            assert r.status_code == 200
    assert jwks.fetches == 1


async def test_entra_v1_issuer_logs_a_hint_and_never_the_token(tmp_path, caplog):
    tok = ek.token(iss=f"https://sts.windows.net/{ek.TENANT}/")
    with caplog.at_level(logging.INFO):
        await status_for(entra_app(tmp_path), tok)
    assert "requestedAccessTokenVersion=2" in caplog.text
    assert tok not in caplog.text and tok.split(".")[1] not in caplog.text


@pytest.mark.parametrize("extra,match", [
    ({"DMA_ENTRA_TENANT_ID": "contoso"}, "DMA_ENTRA_TENANT_ID"),
    ({"DMA_ENTRA_CLIENT_ID": ""}, "DMA_ENTRA_CLIENT_ID"),
    ({"DMA_ENTRA_AUDIENCE": ""}, "DMA_ENTRA_AUDIENCE"),
    ({"DMA_REQUIRED_SCOPES": ""}, "DMA_REQUIRED_SCOPES"),
    ({"DMA_REQUIRED_SCOPES": "api://other/dma.access"}, "DMA_REQUIRED_SCOPES"),
    ({"DMA_ALLOWED_USERS": "someone@example.test"}, "DMA_ALLOWED_USERS"),
    ({"DMA_ALLOWED_GROUPS": "Modellers"}, "DMA_ALLOWED_GROUPS"),
])
def test_entra_mode_refuses_to_start(tmp_path, extra, match):
    env = ek.entra_env(tmp_path, SMX_DIR, CONFIG_DIR, **extra)
    with pytest.raises(AuthConfigError, match=match):
        load_auth(env, load_settings(env).http)


def test_entra_scopes_accept_short_and_full_names(tmp_path):
    env = ek.entra_env(tmp_path, SMX_DIR, CONFIG_DIR, DMA_REQUIRED_SCOPES=f"dma.access, {ek.AUDIENCE}/dma.admin")
    cfg = load_auth(env, load_settings(env).http)
    assert cfg.required_scopes == [f"{ek.AUDIENCE}/dma.access", f"{ek.AUDIENCE}/dma.admin"]
    assert cfg.issuer == ek.ISSUER and cfg.jwks_url.endswith(f"/{ek.TENANT}/discovery/v2.0/keys")


def test_redaction_keeps_args_for_uvicorns_access_formatter(tmp_path):
    from uvicorn.logging import AccessFormatter

    install_redaction({"DMA_DEV_TOKEN": DEV_TOKEN})
    record = logging.getLogger("uvicorn.access").makeRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", "POST", f"/mcp?t={DEV_TOKEN}", "1.1", 200), None)
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)
    assert line.startswith('127.0.0.1:5000 - "POST /mcp?t=[redacted] HTTP/1.1" 200')
    assert DEV_TOKEN not in line
