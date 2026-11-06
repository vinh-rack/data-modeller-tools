"""Settings from env vars and YAML config. Relative paths resolve against the current directory."""

import getpass
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import yaml

TRANSPORTS = ("stdio", "http")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


@dataclass(frozen=True)
class HttpSettings:
    """Streamable HTTP options. Only read when DMA_TRANSPORT=http."""

    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"
    # The canonical URL of this MCP server: the resource in Protected Resource Metadata and the token audience clients ask for.
    resource_server_url: str = "https://127.0.0.1/mcp"
    allowed_hosts: list[str] = field(default_factory=list)
    allowed_origins: list[str] = field(default_factory=list)
    proxy_headers: bool = True


@dataclass(frozen=True)
class Settings:
    smx_dir: Path
    dbt_manifest: Path | None
    dbt_repo: Path | None
    dbt_pull: str
    output_dir: Path
    config_dir: Path
    kb_dir: Path
    transport: str
    user: str
    data_dir: Path | None = None
    http: HttpSettings = field(default_factory=HttpSettings)


def _os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw not in ("1", "0", "true", "false", "yes", "no"):
        raise ValueError(f"{name} must be 1 or 0, got {raw!r}")
    return raw in ("1", "true", "yes")


def load_http(env: Mapping[str, str]) -> HttpSettings:
    host = env.get("DMA_HTTP_HOST", "").strip() or "127.0.0.1"
    raw_port = env.get("DMA_HTTP_PORT", "").strip() or "8000"
    if not raw_port.isdigit() or not 0 < int(raw_port) < 65536:
        raise ValueError(f"DMA_HTTP_PORT must be a port number, got {raw_port!r}")
    path = env.get("DMA_HTTP_PATH", "").strip() or "/mcp"
    if not path.startswith("/") or path == "/":
        raise ValueError(f"DMA_HTTP_PATH must start with / and name a path, got {path!r}")
    resource = env.get("DMA_RESOURCE_SERVER_URL", "").strip() or f"https://{host}{path}"
    parts = urlsplit(resource)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.fragment:
        raise ValueError(f"DMA_RESOURCE_SERVER_URL must be an absolute http(s) URL without a fragment, got {resource!r}")
    # Without explicit lists, accept the resource URL's own host plus loopback, so DNS rebinding protection stays on.
    hosts = _csv(env.get("DMA_ALLOWED_HOSTS")) or [parts.netloc, "127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = _csv(env.get("DMA_ALLOWED_ORIGINS")) or [f"{parts.scheme}://{parts.netloc}", "http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    return HttpSettings(host=host, port=int(raw_port), path=path, resource_server_url=resource.rstrip("/"), allowed_hosts=hosts, allowed_origins=origins, proxy_headers=_flag(env, "DMA_PROXY_HEADERS", True))


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    manifest = env.get("DMA_DBT_MANIFEST", "").strip()
    pull = env.get("DMA_DBT_PULL", "").strip().lower() or "auto"
    if pull not in ("auto", "off"):
        raise ValueError(f"DMA_DBT_PULL must be auto or off, got {pull!r}")
    transport = env.get("DMA_TRANSPORT", "").strip().lower() or "stdio"
    if transport not in TRANSPORTS:
        raise ValueError(f"DMA_TRANSPORT must be one of {list(TRANSPORTS)}, got {transport!r}")
    # One data root (the container uses /data) gives the defaults for output, smx, kb and the dbt clone.
    # Unset, the v1 defaults apply unchanged. An explicit DMA_*_DIR or DMA_DBT_REPO always wins.
    data = env.get("DMA_DATA_DIR", "").strip()
    root = Path(data).resolve() if data else None
    repo = env.get("DMA_DBT_REPO", "").strip() or (str(root / "dbt") if root else "")
    return Settings(
        smx_dir=Path(env.get("DMA_SMX_DIR") or (root / "smx" if root else "tests/fixtures/smx")).resolve(),
        dbt_manifest=Path(manifest).resolve() if manifest else None,
        dbt_repo=Path(repo).resolve() if repo else None,
        dbt_pull=pull,
        output_dir=Path(env.get("DMA_OUTPUT_DIR") or (root / "output" if root else "output")).resolve(),
        config_dir=Path(env.get("DMA_CONFIG_DIR", "config")).resolve(),
        kb_dir=Path(env.get("DMA_KB_DIR") or (root / "kb" if root else "kb")).resolve(),
        transport=transport,
        # Recorded on every gate decision in stdio mode. The server cannot verify it, so it is an audit label, not authentication.
        # HTTP mode takes the user from the verified token instead and ignores this.
        user=env.get("DMA_USER", "").strip() or _os_user(),
        data_dir=root,
        http=load_http(env) if transport == "http" else HttpSettings(),
    )


def load_gates(config_dir: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    """gates.yaml, every template file its stages name, and the output SMX template, all from the same config folder."""
    gates = load_yaml(config_dir, "gates.yaml")
    names = {s["template"] for flow in (gates.get("flows") or {}).values() for s in flow if isinstance(s, dict) and s.get("template")}
    return gates, {n: load_yaml(config_dir, n) for n in sorted(names)}, load_yaml(config_dir, "smx_template.yaml")


def load_yaml(config_dir: Path, name: str) -> dict[str, Any]:
    path = config_dir / name
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
