"""Settings from env vars and YAML config. Relative paths resolve against the current directory."""

import getpass
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


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


def _os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    manifest = env.get("DMA_DBT_MANIFEST", "").strip()
    repo = env.get("DMA_DBT_REPO", "").strip()
    pull = env.get("DMA_DBT_PULL", "").strip().lower() or "auto"
    if pull not in ("auto", "off"):
        raise ValueError(f"DMA_DBT_PULL must be auto or off, got {pull!r}")
    return Settings(
        smx_dir=Path(env.get("DMA_SMX_DIR", "tests/fixtures/smx")).resolve(),
        dbt_manifest=Path(manifest).resolve() if manifest else None,
        dbt_repo=Path(repo).resolve() if repo else None,
        dbt_pull=pull,
        output_dir=Path(env.get("DMA_OUTPUT_DIR", "output")).resolve(),
        config_dir=Path(env.get("DMA_CONFIG_DIR", "config")).resolve(),
        kb_dir=Path(env.get("DMA_KB_DIR", "kb")).resolve(),
        transport=env.get("DMA_TRANSPORT", "stdio").strip() or "stdio",
        # Recorded on every gate decision. The server cannot verify it, so it is an audit label, not authentication.
        user=env.get("DMA_USER", "").strip() or _os_user(),
    )


def load_gates(config_dir: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """gates.yaml plus every template file its stages name, loaded from the same config folder."""
    gates = load_yaml(config_dir, "gates.yaml")
    names = {s["template"] for flow in (gates.get("flows") or {}).values() for s in flow if isinstance(s, dict) and s.get("template")}
    return gates, {n: load_yaml(config_dir, n) for n in sorted(names)}


def load_yaml(config_dir: Path, name: str) -> dict[str, Any]:
    path = config_dir / name
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
