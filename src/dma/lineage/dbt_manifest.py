"""Upstream bronze dependencies of a dbt model, from manifest.json (schema v12) depends_on.nodes."""

import json
from collections import deque
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class ManifestError(ValueError):
    pass


class BronzeSource(BaseModel):
    unique_id: str
    table: str
    source_name: str | None = None


class DbtUpstream(BaseModel):
    model: str
    unique_id: str
    bronze_tables: list[BronzeSource]
    via_models: list[str]
    missing_nodes: list[str]
    manifest_schema: str | None


class DbtManifest:
    def __init__(self, data: dict[str, Any]):
        self.nodes: dict[str, Any] = data.get("nodes", {})
        self.sources: dict[str, Any] = data.get("sources", {})
        self.schema_version: str | None = data.get("metadata", {}).get("dbt_schema_version")

    @classmethod
    def load(cls, path: Path) -> "DbtManifest":
        if not path.is_file():
            raise ManifestError(f"dbt manifest not found: {path}. Set DMA_DBT_MANIFEST.")
        with path.open(encoding="utf-8") as fh:
            return cls(json.load(fh))

    def find_model(self, model_name: str) -> str:
        if model_name in self.nodes:
            return model_name
        wanted = model_name.lower()
        hits = [
            uid for uid, n in self.nodes.items()
            if n.get("resource_type") in ("model", "snapshot", "seed")
            and wanted in {str(n.get("name", "")).lower(), str(n.get("alias") or "").lower()}
        ]
        if not hits:
            raise ManifestError(f"no dbt model named '{model_name}' in manifest")
        if len(hits) > 1:
            raise ManifestError(f"model name '{model_name}' is ambiguous: {sorted(hits)}. Pass the unique_id.")
        return hits[0]

    def upstream(self, model_name: str) -> DbtUpstream:
        start = self.find_model(model_name)
        bronze: dict[str, BronzeSource] = {}
        via: list[str] = []
        missing: list[str] = []
        seen = {start}
        queue = deque(self.nodes[start].get("depends_on", {}).get("nodes", []))
        while queue:
            uid = queue.popleft()
            if uid in seen:
                continue
            seen.add(uid)
            if uid in self.sources:
                s = self.sources[uid]
                table = ".".join(p for p in (s.get("schema"), s.get("identifier") or s.get("name")) if p)
                bronze[uid] = BronzeSource(unique_id=uid, table=table.lower(), source_name=s.get("source_name"))
            elif uid in self.nodes:
                via.append(uid)
                queue.extend(self.nodes[uid].get("depends_on", {}).get("nodes", []))
            else:
                missing.append(uid)
        return DbtUpstream(
            model=self.nodes[start].get("name", start), unique_id=start,
            bronze_tables=sorted(bronze.values(), key=lambda b: b.table),
            via_models=sorted(via), missing_nodes=sorted(missing), manifest_schema=self.schema_version,
        )
