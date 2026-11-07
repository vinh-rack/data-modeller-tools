"""Authentication for the HTTP transport only. stdio takes credentials from the local environment and never loads this.

DMA_AUTH=dev: one static bearer token for local Docker testing.

The verified token of each request names the caller (dma.identity): the gate user, the session owner and the
artifact folder all come from it, never from DMA_USER or a tool argument. Tokens are never logged.
"""

import hmac
import logging
import re
import traceback
from dataclasses import dataclass, field
from typing import Any, Mapping

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

from dma.config import LOCAL_HOSTS, HttpSettings
from dma.identity import Caller, CallerUnknown
from dma.writer import OWNER_RE

log = logging.getLogger("dma.auth")

AUTH_MODES = ("dev",)
MIN_DEV_TOKEN = 32
# Env vars whose values are redacted from every log record, whatever logger or handler writes it.
SECRET_ENV = ("DMA_DEV_TOKEN", "PBI_ACCESS_TOKEN", "DMA_ENTRA_CLIENT_SECRET", "DMA_DBT_GIT_TOKEN")


class AuthConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AuthConfig:
    mode: str
    resource_server_url: str
    required_scopes: list[str] = field(default_factory=list)
    dev_token: str = field(default="", repr=False)
    dev_user_id: str = "dev"
    dev_user_name: str = "Dev user"


def load_auth(env: Mapping[str, str], http: HttpSettings) -> AuthConfig:
    """Read and check the auth settings for HTTP mode. Raises AuthConfigError with a message for the operator."""
    mode = env.get("DMA_AUTH", "").strip().lower()
    if mode not in AUTH_MODES:
        raise AuthConfigError(f"DMA_TRANSPORT=http needs DMA_AUTH, one of {list(AUTH_MODES)}, got {mode or 'nothing'!r}")
    token = env.get("DMA_DEV_TOKEN", "").strip()
    if len(token) < MIN_DEV_TOKEN:
        raise AuthConfigError(f"DMA_AUTH=dev needs DMA_DEV_TOKEN of at least {MIN_DEV_TOKEN} characters, e.g. from: python -c \"import secrets; print(secrets.token_urlsafe(32))\"")
    user_id = env.get("DMA_DEV_USER_ID", "").strip() or "dev"
    if not OWNER_RE.match(user_id):
        raise AuthConfigError(f"DMA_DEV_USER_ID must match {OWNER_RE.pattern}, got {user_id!r}")
    on_network = env.get("DMA_ALLOW_DEV_AUTH_ON_NETWORK", "").strip() == "1"
    if http.host not in LOCAL_HOSTS and not on_network:
        raise AuthConfigError(f"DMA_AUTH=dev only binds to loopback, but DMA_HTTP_HOST is {http.host!r}. Set DMA_ALLOW_DEV_AUTH_ON_NETWORK=1 only for local Docker with the port published on 127.0.0.1.")
    log.warning("!" * 72)
    log.warning("DMA_AUTH=dev: one static bearer token, for local testing only. Never use it on a shared or hosted server.")
    if on_network:
        log.warning("DMA_ALLOW_DEV_AUTH_ON_NETWORK=1: listening on %s. Publish the port on 127.0.0.1 only.", http.host)
    log.warning("!" * 72)
    return AuthConfig(mode="dev", resource_server_url=http.resource_server_url, dev_token=token, dev_user_id=user_id, dev_user_name=env.get("DMA_DEV_USER_NAME", "").strip() or "Dev user")


class DevTokenVerifier(TokenVerifier):
    def __init__(self, cfg: AuthConfig):
        self._token = cfg.dev_token.encode("utf-8")
        self._claims = {"oid": cfg.dev_user_id, "name": cfg.dev_user_name}

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token.encode("utf-8"), self._token):
            return None
        return AccessToken(token=token, client_id="dma-dev", scopes=[], subject=self._claims["oid"], claims=dict(self._claims))


def build_auth(cfg: AuthConfig) -> dict[str, Any]:
    """The MCPServer arguments for this mode: a token verifier and the AuthSettings that publish Protected Resource Metadata."""
    verifier = DevTokenVerifier(cfg)
    origin = re.match(r"^[a-z]+://[^/]+", cfg.resource_server_url).group(0)
    # Dev mode has no authorization server; the metadata names the server itself, so an OAuth attempt fails cleanly.
    settings = AuthSettings(issuer_url=origin, resource_server_url=cfg.resource_server_url, required_scopes=cfg.required_scopes or None, validate_token_resource=False)
    return {"token_verifier": verifier, "auth": settings}


def token_caller() -> Caller:
    """The caller of the current HTTP request, from its verified access token. Never falls back to another identity."""
    tok = get_access_token()
    claims = (tok.claims or {}) if tok else {}
    oid = claims.get("oid") or (tok.subject if tok else None)
    if not tok or not oid:
        raise CallerUnknown("no verified caller for this request: HTTP mode needs a valid bearer token")
    name = claims.get("name") or claims.get("preferred_username") or oid
    return Caller(id=str(oid), label=f"{name} ({oid})")


_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*")
_installed: list[str] | None = None


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, "[redacted]")
    return _JWT.sub("[redacted-jwt]", _BEARER.sub(r"\1[redacted]", text))


def install_redaction(env: Mapping[str, str]) -> None:
    """Redact bearer tokens, JWTs and secret env values from every log record, including exception text. It works at
    record creation, so it covers uvicorn's own loggers too, which do not propagate to the root logger."""
    global _installed
    secrets = sorted({v.strip() for k in SECRET_ENV if len(v := env.get(k, "").strip()) >= 8}, key=len, reverse=True)
    if _installed is not None:
        _installed[:] = secrets
        return
    _installed = secrets
    base = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base(*args, **kwargs)
        try:
            record.msg, record.args = redact(record.getMessage(), _installed), ()
        except Exception:
            record.msg, record.args = redact(str(record.msg), _installed), ()
        if record.exc_info and not record.exc_text:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)).rstrip("\n"), _installed)
        return record

    logging.setLogRecordFactory(factory)
