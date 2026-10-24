"""Pydantic models shared by the index, rules, lineage and tools."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Layer(StrEnum):
    BRONZE = "bronze"
    SILVER_STAGING = "silver_staging"
    SILVER_IDM = "silver_idm"
    GOLD = "gold"

    @property
    def order(self) -> int:
        return _LAYER_ORDER[self]


_LAYER_ORDER = {Layer.BRONZE: 0, Layer.SILVER_STAGING: 1, Layer.SILVER_IDM: 2, Layer.GOLD: 3}

Severity = Literal["error", "warning", "info"]


class SourceRef(BaseModel):
    """A table referenced as a source. schema_name is None when the SMX cell was unqualified."""

    schema_name: str | None = None
    table: str
    source_system: str | None = None

    @property
    def qualified(self) -> str:
        return f"{self.schema_name}.{self.table}" if self.schema_name else self.table


class Column(BaseModel):
    name: str
    data_type: str | None = None
    is_pk: bool = False
    is_fk: bool = False
    source_table: str | None = None
    source_column: str | None = None
    rules: str | None = None
    row: int | None = None


class Table(BaseModel):
    model: str
    layer: Layer
    schema_name: str
    name: str
    columns: list[Column] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    rules_text: str = ""
    bu: str | None = None
    sheet: str
    row: int

    @property
    def qualified(self) -> str:
        return f"{self.schema_name}.{self.name}" if self.schema_name else self.name

    @property
    def grain(self) -> list[str]:
        """Declared grain: the sorted set of PK column names."""
        return sorted({c.name.lower() for c in self.columns if c.is_pk})


class Finding(BaseModel):
    rule_id: str
    severity: Severity
    layer: Layer | None
    table: str
    column: str | None = None
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ModelInfo(BaseModel):
    name: str
    path: str
    sheets: dict[str, Layer] = Field(default_factory=dict)
    table_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class TableSummary(BaseModel):
    model: str
    layer: Layer
    table: str
    bu: str | None
    grain: list[str]
    column_count: int
