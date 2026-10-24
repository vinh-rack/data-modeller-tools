"""Schema classification driven by config/rules.yaml: schema to layer, schema to BU."""

import re
from typing import Any

from dma.models import Layer, SourceRef


class SchemaClassifier:
    def __init__(self, rules_cfg: dict[str, Any]):
        self._layer_patterns = [
            (Layer(layer), re.compile(p))
            for layer, patterns in rules_cfg.get("layer_schema_patterns", {}).items()
            for p in patterns
        ]
        self._system_layers = {k.lower(): Layer(v) for k, v in rules_cfg.get("source_system_layers", {}).items()}
        self._bu_rules = [(re.compile(r["pattern"]), r) for r in rules_cfg.get("business_units", [])]

    def layer_of_schema(self, schema: str | None) -> Layer | None:
        if not schema:
            return None
        s = schema.lower()
        for layer, rx in self._layer_patterns:
            if rx.search(s):
                return layer
        return None

    def layer_of_source(self, src: SourceRef) -> Layer | None:
        layer = self.layer_of_schema(src.schema_name)
        if layer is None and src.source_system:
            layer = self.layer_of_schema(src.source_system) or self._system_layers.get(src.source_system.lower())
        return layer

    def bu_of_schema(self, schema: str | None) -> str | None:
        if not schema:
            return None
        s = schema.lower()
        for rx, rule in self._bu_rules:
            m = rx.search(s)
            if m:
                if "bu" in rule:
                    return rule["bu"]
                return m.groupdict().get("bu")
        return None

    def normalise_source(self, src: SourceRef) -> SourceRef:
        """Use the source system as schema when it is itself a known schema (canonical layout: system=slvr_cdm)."""
        if src.schema_name is None and src.source_system and self.layer_of_schema(src.source_system):
            return SourceRef(schema_name=src.source_system.lower(), table=src.table, source_system=src.source_system)
        return src
