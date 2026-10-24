"""Shared pieces for metadata-only connectors. Connectors never run DDL or DML."""

import os
from typing import Mapping


class ConnectorNotConfigured(RuntimeError):
    pass


class MetadataConnector:
    """Reads catalog metadata only. Subclasses list the env vars they need."""

    name = "connector"
    env_vars: tuple[str, ...] = ()

    def __init__(self, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        self.config = {k: env.get(k, "") for k in self.env_vars}

    def is_configured(self) -> bool:
        return all(self.config.values())

    def missing(self) -> list[str]:
        return [k for k, v in self.config.items() if not v]

    def _not_ready(self) -> ConnectorNotConfigured:
        return ConnectorNotConfigured(f"{self.name} connector is a stub in v0. Missing env vars: {self.missing()}")
