"""Tool logic as plain methods. Transport-agnostic: server.py only registers these."""

from collections import Counter
from functools import cached_property
from typing import Annotated

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from dma.config import Settings, load_yaml
from dma.index.model_index import ModelIndex, summarise
from dma.lineage.dbt_manifest import DbtManifest, DbtUpstream, ManifestError
from dma.lineage.sql_lineage import LineageError, SqlLineage, column_lineage
from dma.models import Finding, ModelInfo, Table, TableSummary
from dma.rules import RuleContext, run_rules
from dma.writer import ArtifactKind, ArtifactWriter, WriterError, WrittenArtifact


class ModelList(BaseModel):
    smx_dir: str
    models: list[ModelInfo]


class ModelDetail(BaseModel):
    model: ModelInfo
    tables: list[Table]


class TableList(BaseModel):
    query: str
    tables: list[TableSummary]


class RuleRun(BaseModel):
    model: str
    tables_checked: list[str]
    counts: dict[str, int]
    findings: list[Finding]


class DmaTools:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mapping = load_yaml(settings.config_dir, "smx_mapping.yaml")
        self.rules_cfg = load_yaml(settings.config_dir, "rules.yaml")
        self.writer = ArtifactWriter(settings.output_dir, protected=[settings.smx_dir])

    @cached_property
    def index(self) -> ModelIndex:
        if not self.settings.smx_dir.is_dir():
            raise ToolError(f"SMX folder not found: {self.settings.smx_dir}. Set DMA_SMX_DIR.")
        return ModelIndex.build(self.settings.smx_dir, self.mapping, self.rules_cfg)

    def _model_tables(self, model_name: str) -> list[Table]:
        try:
            return self.index.get_model(model_name)
        except KeyError as e:
            raise ToolError(str(e.args[0])) from e

    def list_models(self) -> ModelList:
        return ModelList(smx_dir=str(self.settings.smx_dir), models=self.index.list_models())

    def get_model(self, model_name: Annotated[str, Field(description="Model name as returned by list_models (workbook file name without .xlsx).")]) -> ModelDetail:
        tables = self._model_tables(model_name)
        return ModelDetail(model=self.index.models[model_name], tables=tables)

    def find_tables_by_bronze(self, bronze_table: Annotated[str, Field(description="Bronze table, schema.table or bare table name, e.g. brnz_fleetops.booking.")]) -> TableList:
        return TableList(query=bronze_table, tables=[summarise(t) for t in self.index.by_bronze(bronze_table)])

    def find_tables_by_grain(self, grain_text: Annotated[str, Field(description="Comma-separated PK column names, e.g. 'driver_id, booking_month'.")]) -> TableList:
        columns = [c for c in grain_text.replace(";", ",").split(",") if c.strip()]
        if not columns:
            raise ToolError("grain_text must list at least one column name")
        return TableList(query=grain_text, tables=[summarise(t) for t in self.index.by_grain(columns)])

    def run_rules(
        self,
        model_name: Annotated[str, Field(description="Model to check, as returned by list_models.")],
        tables: Annotated[list[str] | None, Field(description="Optional subset of tables (schema.table or bare name). Omit to check the whole model.")] = None,
        column_usage: Annotated[list[str] | None, Field(description="Optional semantic model column usage as schema.table.column entries. Needed for GOLD-001.")] = None,
    ) -> RuleRun:
        targets = self._model_tables(model_name)
        if tables:
            wanted = {n.lower() for n in tables}
            selected = [t for t in targets if t.qualified.lower() in wanted or t.name.lower() in wanted]
            found = {t.qualified.lower() for t in selected} | {t.name.lower() for t in selected}
            unknown = sorted(wanted - found)
            if unknown:
                raise ToolError(f"tables not in model {model_name}: {unknown}")
            targets = selected
        ctx = RuleContext(index=self.index, targets=targets, config=self.rules_cfg, column_usage=set(column_usage) if column_usage is not None else None)
        findings = run_rules(ctx)
        return RuleRun(
            model=model_name,
            tables_checked=[t.qualified for t in targets],
            counts=dict(Counter(f.severity for f in findings)),
            findings=findings,
        )

    def dbt_upstream(self, model_name: Annotated[str, Field(description="dbt model name, alias or unique_id.")]) -> DbtUpstream:
        if self.settings.dbt_manifest is None:
            raise ToolError("DMA_DBT_MANIFEST is not set")
        try:
            return DbtManifest.load(self.settings.dbt_manifest).upstream(model_name)
        except ManifestError as e:
            raise ToolError(str(e)) from e

    def sql_column_lineage(
        self,
        sql: Annotated[str, Field(description="One SELECT statement (CTEs allowed).")],
        column: Annotated[str, Field(description="Output column name to trace.")],
        dialect: Annotated[str, Field(description="redshift, databricks or spark.")] = "databricks",
    ) -> SqlLineage:
        try:
            return column_lineage(sql, column, dialect)
        except LineageError as e:
            raise ToolError(str(e)) from e

    def write_artifact(
        self,
        kind: Annotated[ArtifactKind, Field(description="findings_md, decisions_json, proposed_smx_xlsx (JSON {sheet: [rows]}) or lineage_mermaid.")],
        name: Annotated[str, Field(description="File name stem, letters, digits, '_', '-', '.' only. A timestamp prefix and extension are added.")],
        content: Annotated[str, Field(description="File content. JSON text for decisions_json and proposed_smx_xlsx.")],
    ) -> WrittenArtifact:
        try:
            return self.writer.write(kind, name, content)
        except WriterError as e:
            raise ToolError(str(e)) from e
