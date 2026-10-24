"""Settings from env vars and YAML config. Relative paths resolve against the current directory."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(frozen=True)
class Settings:
    smx_dir: Path
    dbt_manifest: Path | None
    output_dir: Path
    config_dir: Path
    transport: str


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    manifest = env.get("DMA_DBT_MANIFEST", "").strip()
    return Settings(
        smx_dir=Path(env.get("DMA_SMX_DIR", "tests/fixtures/smx")).resolve(),
        dbt_manifest=Path(manifest).resolve() if manifest else None,
        output_dir=Path(env.get("DMA_OUTPUT_DIR", "output")).resolve(),
        config_dir=Path(env.get("DMA_CONFIG_DIR", "config")).resolve(),
        transport=env.get("DMA_TRANSPORT", "stdio").strip() or "stdio",
    )


def load_yaml(config_dir: Path, name: str) -> dict[str, Any]:
    path = config_dir / name
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
