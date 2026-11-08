"""Authentication for the HTTP transport only. stdio takes credentials from the local environment and never loads this.

DMA_AUTH=entra: Microsoft Entra ID v2 access tokens for this API (signature from the tenant JWKS, issuer, tenant,
audience, expiry, delegated scope), plus an optional allowlist of users (oid) and groups.
DMA_AUTH=dev: one static bearer token for local Docker testing.

The verified token of each request names the caller (dma.identity): the gate user, the session owner and the
artifact folder all come from it, never from DMA_USER or a tool argument. Tokens are never logged.
"""

import hmac
import logging
import re
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

import anyio.to_thread
import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

from dma.config import LOCAL_HOSTS, HttpSettings
from dma.identity import Caller, CallerUnknown
from dma.writer import OWNER_RE

log = logging.getLogger("dma.auth")

AUTH_MODES = ("entra", "dev")
MIN_DEV_TOKEN = 32
# Env vars whose values are redacted from every log record, whatever logger or handler writes it.
SECRET_ENV = ("DMA_DEV_TOKEN", "PBI_ACCESS_TOKEN", "DMA_ENTRA_CLIENT_SECRET", "DMA_DBT_GIT_TOKEN")
ENTRA_AUTHORITY = "https://login.microsoftonline.com"
# Entra rotates signing keys; about once a day is the documented check frequency (access-tokens, "Validate the signature").
JWKS_LIFESPAN_SECONDS = 24 * 3600
CLOCK_SKEW_SECONDS = 60


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
    tenant_id: str = ""
    client_id: str = ""
    audience: str = ""
    allowed_users: frozenset[str] = frozenset()
    allowed_groups: frozenset[str] = frozenset()

    @property
    def issuer(self) -> str:
        return f"{ENTRA_AUTHORITY}/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"{ENTRA_AUTHORITY}/{self.tenant_id}/discovery/v2.0/keys"


def _guid(name: str, value: str) -> str:
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        raise AuthConfigError(f"{name} must be a GUID, got {value.strip()!r}") from None


def _list(value: str | None) -> list[str]:
    return [v for v in re.split(r"[\s,]+", value or "") if v]


def load_auth(env: Mapping[str, str], http: HttpSettings) -> AuthConfig:
    """Read and check the auth settings for HTTP mode. Raises AuthConfigError with a message for the operator."""
    mode = env.get("DMA_AUTH", "").strip().lower()
    if mode not in AUTH_MODES:
        raise AuthConfigError(f"DMA_TRANSPORT=http needs DMA_AUTH, one of {list(AUTH_MODES)}, got {mode or 'nothing'!r}")
    if mode == "entra":
        return _load_entra(env, http)
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


def _load_entra(env: Mapping[str, str], http: HttpSettings) -> AuthConfig:
    tenant = _guid("DMA_ENTRA_TENANT_ID", env.get("DMA_ENTRA_TENANT_ID", ""))
    client = _guid("DMA_ENTRA_CLIENT_ID", env.get("DMA_ENTRA_CLIENT_ID", ""))
    audience = env.get("DMA_ENTRA_AUDIENCE", "").strip().rstrip("/")
    if not audience:
        raise AuthConfigError("DMA_AUTH=entra needs DMA_ENTRA_AUDIENCE, the App ID URI of the API registration, e.g. api://<client id>")
    scopes = _list(env.get("DMA_REQUIRED_SCOPES"))
    if not scopes:
        raise AuthConfigError("DMA_AUTH=entra needs DMA_REQUIRED_SCOPES, e.g. api://<client id>/dma.access (a short name such as dma.access also works)")
    # The token's scp claim holds short names; full names are what a client can request and what the metadata advertises.
    full = [s if "/" in s else f"{audience}/{s}" for s in scopes]
    if any(not s.startswith(f"{audience}/") for s in full):
        raise AuthConfigError(f"every DMA_REQUIRED_SCOPES entry must be a scope of {audience}, got {scopes}")
    users = frozenset(_guid("DMA_ALLOWED_USERS", u) for u in _list(env.get("DMA_ALLOWED_USERS")))
    groups = frozenset(_guid("DMA_ALLOWED_GROUPS", g) for g in _list(env.get("DMA_ALLOWED_GROUPS")))
    return AuthConfig(mode="entra", resource_server_url=http.resource_server_url, required_scopes=full, tenant_id=tenant, client_id=client, audience=audience, allowed_users=users, allowed_groups=groups)


class DevTokenVerifier(TokenVerifier):
    def __init__(self, cfg: AuthConfig):
        self._token = cfg.dev_token.encode("utf-8")
        self._claims = {"oid": cfg.dev_user_id, "name": cfg.dev_user_name}

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token.encode("utf-8"), self._token):
            return None
        return AccessToken(token=token, client_id="dma-dev", scopes=[], subject=self._claims["oid"], claims=dict(self._claims))


class EntraTokenVerifier(TokenVerifier):
    """Validates Entra ID v2 access tokens issued for this API (learn.microsoft.com access-tokens, "Validate tokens").

    Any failure returns None, so the SDK answers 401. A valid token without the required delegated scope, an app-only
    token, or a caller outside the allowlist is returned with no scopes, so the SDK answers 403 insufficient_scope.
    """

    def __init__(self, cfg: AuthConfig, jwks_client: jwt.PyJWKClient | None = None):
        self.cfg = cfg
        self._jwks = jwks_client or jwt.PyJWKClient(cfg.jwks_url, cache_keys=True, lifespan=JWKS_LIFESPAN_SECONDS)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            # PyJWKClient fetches keys with blocking urllib, so validation runs on a worker thread.
            return await anyio.to_thread.run_sync(self._verify, token)
        except jwt.InvalidIssuerError:
            log.warning("token refused: issuer is not %s; a v1 token (sts.windows.net) means the API registration needs requestedAccessTokenVersion=2", self.cfg.issuer)
        except (jwt.PyJWTError, ValueError, KeyError) as e:
            # The exception text never contains the token, only what failed.
            log.info("token refused: %s", e.__class__.__name__)
        return None

    def _verify(self, token: str) -> AccessToken:
        if jwt.get_unverified_header(token).get("alg") != "RS256":
            raise jwt.InvalidAlgorithmError("only RS256 is accepted")
        key = self._jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=[self.cfg.client_id, self.cfg.audience], issuer=self.cfg.issuer,
            leeway=CLOCK_SKEW_SECONDS, options={"require": ["exp", "iss", "aud", "tid", "oid"]},
        )
        if str(claims.get("tid", "")).lower() != self.cfg.tenant_id:
            raise jwt.InvalidTokenError("tid does not match the tenant")
        oid = str(claims["oid"])
        scp = str(claims.get("scp") or "").split()
        scopes = [f"{self.cfg.audience}/{s}" for s in scp]
        if not scp:
            log.info("token refused: no delegated scope (app-only tokens are not accepted), oid %s", oid)
        elif not self._allowed(claims):
            log.warning("caller %s is not in DMA_ALLOWED_USERS or DMA_ALLOWED_GROUPS", oid)
            scopes = []
        kept = {k: claims[k] for k in ("iss", "tid", "oid", "name", "preferred_username") if k in claims}
        return AccessToken(token=token, client_id=str(claims.get("azp") or claims.get("appid") or "unknown"), scopes=scopes, expires_at=int(claims["exp"]), subject=oid, claims=kept)

    def _allowed(self, claims: dict[str, Any]) -> bool:
        if not self.cfg.allowed_users and not self.cfg.allowed_groups:
            return True
        if str(claims["oid"]).lower() in self.cfg.allowed_users:
            return True
        if "groups" in (claims.get("_claim_names") or {}):
            # Group overage: the token lists no groups. Without a Graph call they cannot be checked, so the caller is refused.
            log.warning("caller %s has too many groups for the token (overage); allow them by oid or use app roles", claims["oid"])
            return False
        return bool({str(g).lower() for g in claims.get("groups") or []} & self.cfg.allowed_groups)


def build_auth(cfg: AuthConfig, jwks_client: jwt.PyJWKClient | None = None) -> dict[str, Any]:
    """The MCPServer arguments for this mode: a token verifier and the AuthSettings that publish Protected Resource Metadata."""
    if cfg.mode == "entra":
        verifier: TokenVerifier = EntraTokenVerifier(cfg, jwks_client)
        issuer = cfg.issuer
    else:
        verifier = DevTokenVerifier(cfg)
        # Dev mode has no authorization server; the metadata names the server itself, so an OAuth attempt fails cleanly.
        issuer = re.match(r"^[a-z]+://[^/]+", cfg.resource_server_url).group(0)
    # The verifier checks the audience itself (Entra binds tokens by scope, not by the RFC 8707 resource), so the SDK's
    # resource check stays off. required_scopes are full names, which the metadata advertises as scopes_supported.
    settings = AuthSettings(issuer_url=issuer, resource_server_url=cfg.resource_server_url, required_scopes=cfg.required_scopes or None, validate_token_resource=False)
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

    def clean(value: Any) -> Any:
        # Strings are redacted in place. Anything else is kept as is unless its text holds a secret, so formatters that
        # unpack the args (uvicorn's access log does) still get the same shape.
        if isinstance(value, str):
            return redact(value, _installed)
        text = str(value)
        cleaned = redact(text, _installed)
        return cleaned if cleaned != text else value

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base(*args, **kwargs)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg, _installed)
        if isinstance(record.args, tuple):
            record.args = tuple(clean(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: clean(v) for k, v in record.args.items()}
        # A secret split across the message and its args ("Bearer %s") only shows once formatted: flatten that one record.
        try:
            formatted = record.getMessage()
        except Exception:
            formatted = None
        if formatted is not None and redact(formatted, _installed) != formatted:
            record.msg, record.args = redact(formatted, _installed), ()
        if record.exc_info and not record.exc_text:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)).rstrip("\n"), _installed)
        return record

    logging.setLogRecordFactory(factory)
