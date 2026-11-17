"""Power BI in hosted mode: a per-user on-behalf-of token, never the client's token, never a fallback identity.

MSAL and Power BI are both fakes; nothing leaves the process.
"""

import pytest

import entrakit as ek
from conftest import CONFIG_DIR, SMX_DIR
from dma.auth import AuthConfigError
from dma.config import load_settings
from dma.connectors.obo import PBI_SCOPE, OboConfig, OboTokenProvider, explain, load_obo
from dma.connectors.powerbi import PowerBIAuthError
from dma.server import build_http
from httpkit import mcp_client
from test_powerbi import DATASET, REPORT, ok

REPORT_2 = "66666666-6666-4666-8666-666666666666"


class FakeMsal:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def acquire_token_on_behalf_of(self, user_assertion, scopes, **kw):
        self.calls.append({"user_assertion": user_assertion, "scopes": scopes})
        if self.result is not None:
            return self.result
        return {"access_token": f"pbi-obo-token-{len(self.calls)}", "expires_in": 3600, "token_type": "Bearer"}


def pbi_responses():
    def respond(method, url, headers, body, timeout):
        report = url.rsplit("/", 1)[-1]
        if "/reports/" in url:
            return ok({"id": report, "name": "Synthetic report", "datasetId": DATASET, "reportType": "PowerBIReport"})
        if url.endswith("/datasources"):
            return ok({"value": []})
        return ok({"id": DATASET, "name": "Synthetic model"})

    calls = []

    def recording(method, url, headers, body, timeout):
        calls.append({"url": url, "authorization": headers.get("Authorization")})
        return respond(method, url, headers, body, timeout)

    recording.calls = calls
    return recording


def app_for(tmp_path, msal, send, **extra):
    env = ek.entra_env(tmp_path, SMX_DIR, CONFIG_DIR, PBI_ACCESS_TOKEN="env-token-that-must-never-be-used-0123", **extra)
    return build_http(load_settings(env), env, jwks_client=ek.LocalJWKS(), obo_app=msal, pbi_send=send)


@pytest.mark.anyio
async def test_power_bi_gets_the_obo_token_never_the_client_token(tmp_path):
    msal, send = FakeMsal(), pbi_responses()
    client_token = ek.token(oid=ek.USER_A)
    async with mcp_client(app_for(tmp_path, msal, send), headers={"authorization": f"Bearer {client_token}"}) as c:
        first = await c.call_tool("pbi_get_report", {"report_id": REPORT})
        second = await c.call_tool("pbi_get_report", {"report_id": REPORT_2})
    assert not first.is_error and not second.is_error
    assert send.calls and all(x["authorization"] == "Bearer pbi-obo-token-1" for x in send.calls)
    assert all(client_token not in x["authorization"] and "env-token" not in x["authorization"] for x in send.calls)
    # One OBO exchange, with the user's own assertion and the Power BI scope; the second report used the cached token.
    assert msal.calls == [{"user_assertion": client_token, "scopes": [PBI_SCOPE]}]


@pytest.mark.anyio
async def test_each_user_gets_their_own_token_and_cache(tmp_path):
    msal, send = FakeMsal(), pbi_responses()
    tok_a, tok_b = ek.token(oid=ek.USER_A, name="User A"), ek.token(oid=ek.USER_B, name="User B")
    async with mcp_client(app_for(tmp_path, msal, send), headers={"authorization": f"Bearer {tok_a}"}) as a:
        await a.call_tool("pbi_get_report", {"report_id": REPORT})
    a_calls = len(send.calls)
    async with mcp_client(app_for(tmp_path, msal, send), headers={"authorization": f"Bearer {tok_b}"}) as b:
        await b.call_tool("pbi_get_report", {"report_id": REPORT})
    assert [c["user_assertion"] for c in msal.calls] == [tok_a, tok_b]
    # B's request went to Power BI with B's token instead of reading A's cached response.
    assert len(send.calls) > a_calls and all(x["authorization"] == "Bearer pbi-obo-token-2" for x in send.calls[a_calls:])


@pytest.mark.anyio
@pytest.mark.parametrize("result,words", [
    ({"error": "invalid_grant", "error_description": "AADSTS65001: The user or administrator has not consented to use the application. Trace ID: x", "suberror": "consent_required"}, "consent"),
    ({"error": "interaction_required", "error_description": "AADSTS50079: multifactor authentication required. Trace ID: x", "claims": "{}"}, "sign in again"),
    ({"error": "invalid_grant", "error_description": "AADSTS50013: Assertion failed signature validation."}, "refused the on-behalf-of"),
])
async def test_obo_failure_is_clear_and_never_falls_back(tmp_path, result, words):
    msal, send = FakeMsal(result), pbi_responses()
    async with mcp_client(app_for(tmp_path, msal, send), headers={"authorization": f"Bearer {ek.token()}"}) as c:
        r = await c.call_tool("pbi_get_report", {"report_id": REPORT})
    assert r.is_error and words in r.content[0].text
    assert "Trace ID" not in r.content[0].text
    # No Power BI call at all: not with another identity, not with PBI_ACCESS_TOKEN.
    assert send.calls == []


def test_cache_is_refreshed_before_expiry():
    now = [1_000_000.0]
    msal = FakeMsal()
    provider = OboTokenProvider(OboConfig(tenant_id=ek.TENANT, client_id=ek.CLIENT_ID, client_secret="x"), app=msal, clock=lambda: now[0])
    assert provider.token_for(ek.USER_A, "assertion-1") == "pbi-obo-token-1"
    now[0] += 3000
    assert provider.token_for(ek.USER_A, "assertion-2") == "pbi-obo-token-1"
    # Within five minutes of expiry, a new exchange runs with the current assertion.
    now[0] += 400
    assert provider.token_for(ek.USER_A, "assertion-3") == "pbi-obo-token-2"
    assert [c["user_assertion"] for c in msal.calls] == ["assertion-1", "assertion-3"]


def test_network_failure_names_only_the_error_class():
    class Broken:
        def acquire_token_on_behalf_of(self, **kw):
            raise ConnectionError(f"failed sending {kw['user_assertion']}")

    provider = OboTokenProvider(OboConfig(tenant_id=ek.TENANT, client_id=ek.CLIENT_ID, client_secret="x"), app=Broken())
    with pytest.raises(Exception) as err:
        provider.token_for(ek.USER_A, "the-user-assertion")
    assert "ConnectionError" in str(err.value) and "the-user-assertion" not in str(err.value)


def test_failure_is_an_auth_error_with_status():
    provider = OboTokenProvider(OboConfig(tenant_id=ek.TENANT, client_id=ek.CLIENT_ID, client_secret="x"), app=FakeMsal({"error": "invalid_grant", "error_description": "AADSTS65001"}))
    with pytest.raises(PowerBIAuthError) as err:
        provider.token_for(ek.USER_A, "a")
    assert err.value.status == 401 and "consent" in str(err.value)


def test_explain_keeps_the_code_and_drops_the_trace():
    text = explain({"error": "invalid_grant", "error_description": "AADSTS700024: Client assertion is not within its valid time range. Trace ID: abc Correlation ID: def"})
    assert "AADSTS700024" in text and "Trace ID" not in text and "Correlation" not in text


@pytest.mark.parametrize("extra,match", [
    ({"DMA_ENTRA_CLIENT_SECRET": ""}, "exactly one"),
    ({"DMA_ENTRA_CERT_PATH": "cert.pem", "DMA_ENTRA_CERT_THUMBPRINT": "AB12"}, "exactly one"),
    ({"DMA_ENTRA_CLIENT_SECRET": "", "DMA_ENTRA_CERT_PATH": "missing.pem", "DMA_ENTRA_CERT_THUMBPRINT": "AB12"}, "PEM"),
    ({"DMA_ENTRA_CLIENT_SECRET": "", "DMA_ENTRA_CERT_PATH": "cert.pem"}, "THUMBPRINT"),
])
def test_obo_credential_is_checked_at_start(tmp_path, extra, match):
    (tmp_path / "cert.pem").write_text("-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    env = ek.entra_env(tmp_path, SMX_DIR, CONFIG_DIR, **{k: (str(tmp_path / v) if k == "DMA_ENTRA_CERT_PATH" else v) for k, v in extra.items()})
    with pytest.raises(AuthConfigError, match=match):
        load_obo(env, ek.TENANT, ek.CLIENT_ID)


def test_obo_scope_default_and_override(tmp_path):
    env = ek.entra_env(tmp_path, SMX_DIR, CONFIG_DIR)
    assert load_obo(env, ek.TENANT, ek.CLIENT_ID).scope == "https://analysis.windows.net/powerbi/api/.default"
    env["PBI_SCOPE"] = "https://analysis.windows.net/powerbi/api/Report.Read.All"
    assert load_obo(env, ek.TENANT, ek.CLIENT_ID).scope.endswith("Report.Read.All")
    assert "synthetic-client-secret" not in repr(load_obo(env, ek.TENANT, ek.CLIENT_ID))
