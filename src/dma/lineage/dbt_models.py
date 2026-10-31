"""Result models shared by the dbt repo reader and the manifest fallback, so dbt_upstream has one shape."""

from typing import Literal

from pydantic import BaseModel, Field


class Unresolved(BaseModel):
    kind: Literal["missing_ref", "missing_source", "jinja_expression", "jinja_tag", "incremental_block"]
    text: str
    path: str
    line: int


class BronzeSource(BaseModel):
    unique_id: str
    table: str
    source_name: str | None = None
    evidence: str | None = Field(default=None, description="file:line where the source() or seed ref() appears")


class DbtUpstream(BaseModel):
    model: str
    unique_id: str
    exists: bool = True
    route: Literal["dbt_model", "data_analysis"] = "dbt_model"
    reader: Literal["repo", "manifest"] = "manifest"
    bronze_tables: list[BronzeSource]
    via_models: list[str]
    missing_nodes: list[str]
    seeds: list[str] = Field(default_factory=list, description="Seeds reached but not counted as bronze (see bronze_resource_types)")
    unresolved: list[Unresolved] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list, description="Model files read, as repo-relative paths")
    manifest_schema: str | None = None
