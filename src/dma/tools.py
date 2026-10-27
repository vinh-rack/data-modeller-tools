"""Tool logic as plain methods. Transport-agnostic: server.py only registers these."""

import json
from collections import Counter
from functools import cached_property
from typing import Annotated, Callable, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from dma.config import Settings, load_yaml
from dma.connectors.powerbi import PbiModelMetadata, PbiReportInfo, PowerBIClient, PowerBIError, model_metadata, report_info
from dma.gate import After, Decision, GateError, GateStore, SessionSummary, SessionView, StageVersion
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


class SessionList(BaseModel):
    sessions: list[SessionSummary]


class CompiledReport(BaseModel):
    session_id: str
    artifacts: list[WrittenArtifact]


def _cell(text: str | None) -> str:
    return (text or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


class DmaTools:
    def __init__(self, settings: Settings, pbi_factory: Callable[[], PowerBIClient] = PowerBIClient.from_env):
        self.settings = settings
        self._pbi_factory = pbi_factory
        self._pbi_client: PowerBIClient | None = None
        self.mapping = load_yaml(settings.config_dir, "smx_mapping.yaml")
        self.rules_cfg = load_yaml(settings.config_dir, "rules.yaml")
        self.writer = ArtifactWriter(settings.output_dir, protected=[settings.smx_dir])
        try:
            self.gate = GateStore(self.writer, load_yaml(settings.config_dir, "gates.yaml"), user=settings.user)
        except GateError as e:
            raise ToolError(str(e)) from e

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

    def _pbi(self) -> PowerBIClient:
        # Built on first use and kept for the life of the server, so its response cache spans the IDE session.
        if self._pbi_client is None:
            try:
                self._pbi_client = self._pbi_factory()
            except PowerBIError as e:
                raise ToolError(str(e)) from e
        return self._pbi_client

    def pbi_get_report(
        self,
        report_id: Annotated[str, Field(description="Power BI report ID (GUID).")],
        workspace_id: Annotated[str | None, Field(description="Workspace ID (GUID). Omit for My workspace.")] = None,
        dataset_id: Annotated[str | None, Field(description="Optional dataset ID (GUID). It must match the report's own dataset, otherwise the call stops.")] = None,
    ) -> PbiReportInfo:
        try:
            return report_info(self._pbi(), report_id, workspace_id, dataset_id)
        except PowerBIError as e:
            raise ToolError(str(e)) from e

    def pbi_model_metadata(
        self,
        report_id: Annotated[str, Field(description="Power BI report ID (GUID). The dataset is taken from the report.")],
        workspace_id: Annotated[str | None, Field(description="Workspace ID (GUID) for a shared workspace, which enables the Arrow INFO route. Omit for My workspace: its group paths return 401.")] = None,
    ) -> PbiModelMetadata:
        try:
            return model_metadata(self._pbi(), report_id, workspace_id)
        except PowerBIError as e:
            raise ToolError(str(e)) from e

    def start_session(
        self,
        flow: Annotated[Literal["review", "build", "lineage"], Field(description="Which playbook this session follows.")],
        scope: Annotated[str, Field(description="What the session covers, e.g. model name and tables, or Power BI report and workspace IDs.")],
    ) -> SessionView:
        try:
            return self.gate.start(flow, scope)
        except GateError as e:
            raise ToolError(str(e)) from e

    def submit_stage(
        self,
        session_id: Annotated[str, Field(description="Session id from start_session.")],
        stage: Annotated[str, Field(description="The stage being presented. Must be the next stage after the last approved one.")],
        content: Annotated[str, Field(description="The full stage output shown to the modeller: output, evidence and assumptions.")],
    ) -> StageVersion:
        try:
            return self.gate.submit(session_id, stage, content)
        except GateError as e:
            raise ToolError(str(e)) from e

    def record_decision(
        self,
        session_id: Annotated[str, Field(description="Session id from start_session.")],
        stage: Annotated[str, Field(description="Stage the modeller decided on.")],
        version: Annotated[int, Field(description="Version returned by submit_stage.")],
        decision: Annotated[Decision, Field(description="The modeller's explicit word only: approve, suggest_changes or disapprove. Never infer it from silence or vague replies.")],
        note: Annotated[str | None, Field(description="Comment (required for suggest_changes) or reason (required for disapprove), in the modeller's words.")] = None,
        then: Annotated[After | None, Field(description="Disapprove only, chosen by the modeller: redo this stage, reopen an earlier stage, or end the session.")] = None,
        reopen_stage: Annotated[str | None, Field(description="Disapprove with then=reopen only: the earlier stage to reopen. It and every later stage are invalidated.")] = None,
    ) -> SessionView:
        try:
            return self.gate.decide(session_id, stage, version, decision, note, then, reopen_stage)
        except GateError as e:
            raise ToolError(str(e)) from e

    def get_session(self, session_id: Annotated[str, Field(description="Session id from start_session or list_sessions.")]) -> SessionView:
        try:
            return self.gate.view(session_id)
        except GateError as e:
            raise ToolError(str(e)) from e

    def list_sessions(self) -> SessionList:
        return SessionList(sessions=self.gate.list_sessions())

    def compile_report(self, session_id: Annotated[str, Field(description="Session whose final stage is approved.")]) -> CompiledReport:
        try:
            outputs = self.gate.approved_outputs(session_id)
            view = self.gate.view(session_id)
            md = [f"# {view.flow.capitalize()} report", "", f"- Session: `{session_id}`", f"- Scope: {_cell(view.scope)}", f"- Started by {view.created_by} at {view.created_at}", "", "## **1. Approved stages**", ""]
            for i, o in enumerate(outputs, 1):
                md += [f"### *1.{i}. {o['stage']} (version {o['version']})*", "", o["content"].rstrip(), ""]
            md += ["## **2. Decision log**", "", "| # | Stage | Version | Decision | Then | Note | User | UTC time |", "|---|---|---|---|---|---|---|---|"]
            for i, e in enumerate(view.decision_log, 1):
                then = e.then + (f" {e.reopen_stage}" if e.reopen_stage else "") if e.then else ""
                md.append(f"| {i} | {e.stage} | {e.version} | {e.decision} | {then} | {_cell(e.note)} | {_cell(e.user)} | {e.ts} |")
            decisions = {
                "session_id": session_id,
                "flow": view.flow,
                "scope": view.scope,
                "approved_stages": [{"stage": o["stage"], "version": o["version"], "sha256": o["sha256"], "content": o["content"]} for o in outputs],
                "decision_log": [e.model_dump() for e in view.decision_log],
            }
            stem = f"{view.flow}_{session_id[:8]}"
            written = [
                self.writer.write("findings_md", f"{stem}_report", "\n".join(md) + "\n"),
                self.writer.write("decisions_json", f"{stem}_decisions", json.dumps(decisions, ensure_ascii=False)),
            ]
            for w in written:
                self.gate.record_artifact(session_id, w.path)
            return CompiledReport(session_id=session_id, artifacts=written)
        except (GateError, WriterError) as e:
            raise ToolError(str(e)) from e

    def write_artifact(
        self,
        kind: Annotated[ArtifactKind, Field(description="findings_md, decisions_json, proposed_smx_xlsx (JSON {sheet: [rows]}) or lineage_mermaid.")],
        name: Annotated[str, Field(description="File name stem, letters, digits, '_', '-', '.' only. A timestamp prefix and extension are added.")],
        content: Annotated[str, Field(description="File content. JSON text for decisions_json and proposed_smx_xlsx. Use only approved stage outputs.")],
        session_id: Annotated[str, Field(description="Session whose final stage is approved. Writing is refused before that.")],
    ) -> WrittenArtifact:
        try:
            self.gate.require_complete(session_id)
            written = self.writer.write(kind, name, content)
            self.gate.record_artifact(session_id, written.path)
            return written
        except (GateError, WriterError) as e:
            raise ToolError(str(e)) from e
