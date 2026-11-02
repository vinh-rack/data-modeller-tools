"""In-memory index of every table across all SMX workbooks in a folder."""

from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from dma.classify import SchemaClassifier
from dma.index.smx_reader import SmxReader
from dma.models import Layer, ModelInfo, Table, TableSummary


class ModelIndex:
    def __init__(self, models: dict[str, ModelInfo], tables: list[Table], classifier: SchemaClassifier):
        self.models = models
        self.tables = tables
        self.classifier = classifier
        self._by_name: dict[str, list[Table]] = defaultdict(list)
        for t in tables:
            self._by_name[t.qualified.lower()].append(t)
            self._by_name[t.name.lower()].append(t)
        self._downstream: dict[str, list[Table]] = defaultdict(list)
        for t in tables:
            for src in t.sources:
                self._downstream[src.qualified.lower()].append(t)
                if src.schema_name:
                    self._downstream[src.table.lower()].append(t)

    @classmethod
    def build(cls, smx_dir: Path, mapping: dict[str, Any], rules_cfg: dict[str, Any], strict: bool = False) -> "ModelIndex":
        reader = SmxReader(mapping)
        classifier = SchemaClassifier(rules_cfg)
        models: dict[str, ModelInfo] = {}
        tables: list[Table] = []
        for path in sorted(Path(smx_dir).glob("*.xlsx")):
            if path.name.startswith("~$"):
                continue
            info, model_tables = reader.read_workbook(path, strict=strict)
            for t in model_tables:
                t.sources = [classifier.normalise_source(s) for s in t.sources]
                t.bu = classifier.bu_of_schema(t.schema_name)
            models[info.name] = info
            tables.extend(model_tables)
        return cls(models, tables, classifier)

    def list_models(self) -> list[ModelInfo]:
        return list(self.models.values())

    def get_model(self, model: str) -> list[Table]:
        if model not in self.models:
            raise KeyError(f"unknown model '{model}'. Known: {sorted(self.models)}")
        return [t for t in self.tables if t.model == model]

    def resolve(self, name: str) -> list[Table]:
        """Tables matching a qualified (schema.table) or bare table name."""
        return list(self._by_name.get(name.lower(), []))

    def by_bronze(self, bronze_table: str) -> list[Table]:
        """Every table that sources from the bronze table, directly or transitively."""
        key = bronze_table.lower()
        start = {key, key.split(".")[-1]} if "." in key else {key}
        seen: dict[int, Table] = {}
        queue = deque(start)
        visited_keys = set(start)
        while queue:
            k = queue.popleft()
            for t in self._downstream.get(k, []):
                if id(t) in seen:
                    continue
                seen[id(t)] = t
                for nxt in (t.qualified.lower(), t.name.lower()):
                    if nxt not in visited_keys:
                        visited_keys.add(nxt)
                        queue.append(nxt)
        return [t for t in seen.values() if t.layer is not Layer.BRONZE]

    def downstream(self, table: Table) -> list[Table]:
        """Tables that list this table as a direct source, by qualified or bare name."""
        found = {id(t): t for k in (table.qualified.lower(), table.name.lower()) for t in self._downstream.get(k, []) if t is not table}
        return list(found.values())

    def by_grain(self, columns: list[str]) -> list[Table]:
        want = sorted({c.strip().lower() for c in columns if c.strip()})
        return [t for t in self.tables if want and t.grain == want]

    def by_bu(self, bu: str | None) -> list[Table]:
        return [t for t in self.tables if t.bu == bu]

    def bronze_sources(self, table: Table, _depth: int = 0) -> list[str]:
        """Bronze tables a table ultimately depends on, following resolved sources up to 10 hops."""
        found: set[str] = set()
        if _depth > 10:
            return []
        for src in table.sources:
            layer = self.classifier.layer_of_source(src)
            if layer is Layer.BRONZE:
                indexed = [p.qualified.lower() for p in self.resolve(src.qualified) if p.layer is Layer.BRONZE]
                if indexed:
                    found.update(indexed)
                else:
                    found.add(src.qualified.lower())
                continue
            for parent in self.resolve(src.qualified):
                if parent is table:
                    continue
                # A source with no known layer (an unqualified name, no source system) can still resolve to an indexed Bronze table.
                if parent.layer is Layer.BRONZE:
                    found.add(parent.qualified.lower())
                else:
                    found.update(self.bronze_sources(parent, _depth + 1))
        return sorted(found)


def summarise(t: Table) -> TableSummary:
    return TableSummary(model=t.model, layer=t.layer, table=t.qualified, bu=t.bu, grain=t.grain, column_count=len(t.columns))
