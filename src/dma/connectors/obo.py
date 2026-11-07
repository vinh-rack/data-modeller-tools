"""Per-user Power BI tokens for the hosted server (DMA_AUTH=entra), from the Entra on-behalf-of flow.

The MCP client's token is issued for this API. It is never sent to Power BI: the MCP spec forbids passing it through,
and Entra's OBO docs say a middle tier must only send it to its intended audience. Instead it is the user assertion in
an OBO exchange (MSAL acquire_token_on_behalf_of) for a delegated Power BI token of the same user, cached per user in
memory until shortly before it expires. On any failure the call stops with a clear message; there is no fallback to
PBI_ACCESS_TOKEN or any other identity.

Scope: https://analysis.windows.net/powerbi/api/.default, as in Microsoft's Execute DAX Queries quickstart (delegated
user token, ms.date 2026-05-04). With .default, Entra grants the delegated Power BI permissions consented on the app.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from mcp.server.auth.middleware.auth_context import get_access_token

from dma.connectors.powerbi import DEFAULT_BASE, DEFAULT_MAX_ROWS, PowerBIAuthError, PowerBIClient, PowerBIError, Send

log = logging.getLogger("dma.obo")

PBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
REFRESH_MARGIN_SECONDS = 300
OBO_401 = (
    "Power BI returned 401 Unauthorized for your own delegated token. Your account may have no access to this report, "
    "dataset or workspace, or the app lacks the Power BI delegated permissions. Ask the workspace admin for access."
)


@dataclass(frozen=True)
class OboConfig:
    tenant_id: str
    client_id: str
    client_secret: str = field(default="", repr=False)
    cert_path: str = ""
    cert_thumbprint: str = ""
    scope: str = PBI_SCOPE


def load_obo(env: Mapping[str, str], tenant_id: str, client_id: str) -> OboConfig:
    """The confidential client credential of the API registration: a client secret, or a certificate (PEM private key plus thumbprint)."""
    from dma.auth import AuthConfigError

    secret = env.get("DMA_ENTRA_CLIENT_SECRET", "").strip()
    cert = env.get("DMA_ENTRA_CERT_PATH", "").strip()
    thumb = env.get("DMA_ENTRA_CERT_THUMBPRINT", "").strip()
    if bool(secret) == bool(cert):
        raise AuthConfigError("DMA_AUTH=entra needs exactly one OBO credential: DMA_ENTRA_CLIENT_SECRET, or DMA_ENTRA_CERT_PATH with DMA_ENTRA_CERT_THUMBPRINT")
    if cert and (not thumb or not Path(cert).is_file()):
        raise AuthConfigError("DMA_ENTRA_CERT_PATH must name a readable PEM private key file, and DMA_ENTRA_CERT_THUMBPRINT must be set")
    scope = env.get("PBI_SCOPE", "").strip() or PBI_SCOPE
    return OboConfig(tenant_id=tenant_id, client_id=client_id, client_secret=secret, cert_path=cert, cert_thumbprint=thumb, scope=scope)


def explain(result: Mapping[str, Any]) -> str:
    """A clear message for an OBO error response. Entra error texts carry AADSTS codes and trace ids, never tokens."""
    error = str(result.get("error") or "unknown_error")
    desc = str(result.get("error_description") or "").split("Trace ID")[0].strip()
    sub = str(result.get("suberror") or "")
    if "AADSTS65001" in desc or sub == "consent_required" or error == "consent_required":
        return f"Power BI consent is missing: an admin must grant consent for the Power BI delegated permissions on the DMA app registration. ({error}: {desc})"
    if error == "interaction_required":
        return f"Entra needs you to sign in again (MFA, Conditional Access or sign-in frequency). Reconnect your MCP client to get a new token, then retry. ({error}: {desc})"
    return f"Entra refused the on-behalf-of exchange for Power BI, so no Power BI call was made. ({error}: {desc})"


class OboTokenProvider:
    def __init__(self, cfg: OboConfig, app: Any = None, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self._app = app
        self._clock = clock
        self._cache: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def _msal(self) -> Any:
        if self._app is None:
            try:
                import msal
            except ImportError:
                raise PowerBIError("DMA_AUTH=entra needs MSAL for Power BI: install the hosted extra (uv sync --extra hosted)") from None
            if self.cfg.client_secret:
                credential: Any = self.cfg.client_secret
            else:
                credential = {"private_key": Path(self.cfg.cert_path).read_text(encoding="utf-8"), "thumbprint": self.cfg.cert_thumbprint}
            self._app = msal.ConfidentialClientApplication(self.cfg.client_id, client_credential=credential, authority=f"https://login.microsoftonline.com/{self.cfg.tenant_id}")
        return self._app

    def token_for(self, oid: str, assertion: str) -> str:
        """A Power BI token for this user, from the cache or a new OBO exchange with the user's own assertion."""
        now = self._clock()
        with self._lock:
            cached = self._cache.get(oid)
        if cached and cached[1] - REFRESH_MARGIN_SECONDS > now:
            return cached[0]
        try:
            result = self._msal().acquire_token_on_behalf_of(user_assertion=assertion, scopes=[self.cfg.scope])
        except PowerBIError:
            raise
        except Exception as e:
            # Network or library failure. The exception class only: its text could echo request data.
            raise PowerBIError(f"could not reach Entra for the on-behalf-of exchange ({e.__class__.__name__}), so no Power BI call was made") from None
        token = (result or {}).get("access_token")
        if not token:
            message = explain(result or {})
            log.warning("OBO failed for %s: %s", oid, message)
            raise PowerBIAuthError(message, status=401)
        expires = now + float(result.get("expires_in") or 0)
        with self._lock:
            self._cache[oid] = (token, expires)
        return token


def client_factory(provider: OboTokenProvider, env: Mapping[str, str] | None = None, send: Send | None = None) -> Callable[[], PowerBIClient]:
    """Builds the Power BI client of the current caller. Its token is resolved per request from that caller's own
    verified access token, and refused if a request ever comes from someone else."""
    env = os.environ if env is None else env
    rows = env.get("PBI_MAX_ROWS", "").strip()
    base = env.get("PBI_API_BASE", "").strip() or DEFAULT_BASE

    def make() -> PowerBIClient:
        owner = _caller_oid()

        def token() -> str:
            oid = _caller_oid()
            if oid != owner:
                raise PowerBIAuthError("this Power BI client belongs to another caller", status=401)
            return provider.token_for(oid, get_access_token().token)

        return PowerBIClient(token, base=base, max_rows=int(rows) if rows else DEFAULT_MAX_ROWS, send=send, auth_message=OBO_401)

    return make


def _caller_oid() -> str:
    tok = get_access_token()
    oid = (tok.claims or {}).get("oid") if tok else None
    if not tok or not oid:
        raise PowerBIAuthError("no verified caller for this request, so no Power BI token can be obtained", status=401)
    return str(oid)
