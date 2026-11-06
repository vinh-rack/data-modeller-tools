"""Tool logic as plain methods. Transport-agnostic: server.py only registers these."""

import json
from collections import Counter
from functools import cached_property
from typing import Annotated, Any, Callable, Literal

import sqlglot
from mcp.server.mcpserver.exceptions import ToolError
from sqlglot import exp
from pydantic import BaseModel, Field

from dma.config import Settings, load_gates, load_yaml
from dma.connectors.sampling import MAX_SAMPLE_ROWS, RowSampler, SamplingUnavailable, UnavailableSampler
from dma.connectors.powerbi import PbiModelMetadata, PbiReportInfo, PowerBIClient, PowerBIError, model_metadata, report_info
from dma.identity import CallerResolver, static_caller
from dma.gate import CODE_DIALECTS, REQUIREMENTS_STAGE, After, Decision, GateError, GateStore, SessionSummary, SessionView, StageVersion
from dma.index.model_index import ModelIndex, summarise
from dma.kb import KbDocument, KbError, KbFile, KnowledgeBase, ReportEntry
from dma.lineage.diagram import Direction, LineageGraph, ReportLineage, report_lineage, table_lineage
from dma.lineage.dbt_manifest import DbtManifest, ManifestError
from dma.lineage.dbt_models import DbtUpstream, Unresolved
from dma.lineage.dbt_pull import DbtPuller, PullResult
from dma.lineage.dbt_repo import DbtColumn, DbtRef, DbtRepo, DbtRepoError, DbtSourceRef
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
    notes: list[str] = Field(default_factory=list)


class KbListing(BaseModel):
    kb_dir: str
    files: list[KbFile]


class DbtModelDetail(BaseModel):
    name: str
    match: str = Field(description="How the name matched: name, alias or source_table")
    path: str
    alias: str | None
    relation: str
    description: str | None
    columns: list[DbtColumn]
    raw_sql: str
    rendered_sql: str
    parses: bool
    parse_error: str | None
    refs: list[DbtRef]
    sources: list[DbtSourceRef]
    unresolved: list[Unresolved]
    bronze_tables: list["BronzeCandidate"] = Field(default_factory=list, description="Provided code only: the Bronze tables it reads")


class BronzeCandidate(BaseModel):
    table: str
    tag: Literal["confirmed", "inferred"] = Field(description="confirmed: a source() or ref() chain resolved in the dbt repo; inferred: a table named in plain SQL or an unknown source, for the modeller to confirm")
    evidence: str


class DbtLookup(BaseModel):
    query: str
    exists: bool
    origin: Literal["repo", "provided"] = "repo"
    route: Literal["dbt_model", "data_analysis", "provided_code"]
    note: str
    models: list[DbtModelDetail]


class DbtRepoStatus(BaseModel):
    configured: bool
    repo: str | None
    project: str | None = None
    pull_mode: str
    this_session_pull: PullResult | None = None
    last_logged_pull: dict | None = None
    commit: str | None = None
    models: int = 0
    sources: int = 0
    seeds: int = 0
    problems: list[str] = Field(default_factory=list)


class SampleResult(BaseModel):
    session_id: str
    table: str
    limit: int
    status: Literal["ok", "not_available"]
    reason: str | None = None
    rows: list[dict] = Field(default_factory=list, description="Only for the data_extraction stage the modeller sees. Never written to disk.")


class SessionList(BaseModel):
    sessions: list[SessionSummary]


class CompiledReport(BaseModel):
    session_id: str
    artifacts: list[WrittenArtifact]


def _provided_bronze(repo: DbtRepo, r: Any, dialect: str, bronze_types: list[str]) -> list[BronzeCandidate]:
    """Bronze tables a pasted model reads: resolved sources and ref chains are confirmed, anything else is inferred."""
    found: dict[str, BronzeCandidate] = {}
    for s in r.sources:
        st = repo.sources.get((s.source.lower(), s.table.lower()))
        rel = st.relation if st else f"{s.source}.{s.table}"
        found.setdefault(rel, BronzeCandidate(table=rel, tag="confirmed" if st else "inferred", evidence=f"{r.path}:{s.line}"))
    for ref in r.refs:
        node = repo.model_node(ref.name, ref.version)
        if node:
            for b in repo.upstream(node.name, bronze_types, dialect).bronze_tables:
                found.setdefault(b.table, BronzeCandidate(table=b.table, tag="confirmed", evidence=f"{r.path}:{ref.line} via {b.evidence}"))
    if not r.sources and not r.refs and r.parses:
        tree = sqlglot.parse_one(r.rendered_sql, read=dialect)
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        for tb in tree.find_all(exp.Table):
            name = ".".join(p for p in (tb.catalog, tb.db, tb.name) if p)
            if name and name.lower() not in ctes and not name.startswith("__dma_"):
                found.setdefault(name, BronzeCandidate(table=name, tag="inferred", evidence=f"{r.path}: table in plain SQL"))
    return sorted(found.values(), key=lambda b: b.table)


def _cell(text: str | None) -> str:
    return (text or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


class DmaTools:
    def __init__(self, settings: Settings, pbi_factory: Callable[[], PowerBIClient] = PowerBIClient.from_env, sampler: RowSampler | None = None, caller: CallerResolver | None = None):
        self.settings = settings
        # stdio: the DMA_USER label. HTTP: server.py passes a resolver that reads the verified token.
        self.caller: CallerResolver = caller or static_caller(settings.user)
        self.sampler = sampler or UnavailableSampler()
        self._pbi_factory = pbi_factory
        self._pbi_client: PowerBIClient | None = None
        self.mapping = load_yaml(settings.config_dir, "smx_mapping.yaml")
        self.rules_cfg = load_yaml(settings.config_dir, "rules.yaml")
        self.kb = KnowledgeBase(settings.kb_dir)
        self.dbt_cfg = load_yaml(settings.config_dir, "dbt.yaml")
        self._dbt_pull: PullResult | None = None
        self.writer = ArtifactWriter(settings.output_dir, protected=[settings.smx_dir])
        try:
            gates, templates, smx_template = load_gates(settings.config_dir)
            self.gate = GateStore(self.writer, gates, user=settings.user, templates=templates, smx_template=smx_template, caller=self.caller)
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
        column_usage: Annotated[list[str] | None, Field(description="Optional semantic model column usage as schema.table.column entries. Needed for GOLD-001. Prefer report_id.")] = None,
        report_id: Annotated[str | None, Field(description="Optional Power BI report ID. The server reads the report's columns and maps them to Gold through the knowledge base report map, for GOLD-001.")] = None,
        workspace_id: Annotated[str | None, Field(description="Workspace ID for report_id. Omit for My workspace.")] = None,
    ) -> RuleRun:
        if report_id and column_usage is not None:
            raise ToolError("give report_id or column_usage, not both")
        notes: list[str] = []
        if report_id:
            column_usage, notes = self._report_usage(report_id, workspace_id)
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
            notes=notes,
        )

    def _report_usage(self, report_id: str, workspace_id: str | None) -> tuple[list[str], list[str]]:
        """Gold columns a report uses, from its Power BI columns and the report map. Unmapped tables are noted, not guessed."""
        try:
            entry = self.kb.report(report_id)
            md = model_metadata(self._pbi(), report_id, workspace_id)
        except (KbError, PowerBIError) as e:
            raise ToolError(str(e)) from e
        usage = sorted({g for c in md.columns if c.table and c.column and (g := entry.gold_column(c.table, c.column))})
        unmapped = sorted({c.table for c in md.columns if c.table and c.table not in entry.tables})
        notes = [f"GOLD-001 usage: {len(usage)} Gold columns from report {report_id}"]
        if unmapped:
            notes.append(f"Power BI tables not in the report map, so their columns count as unknown usage: {', '.join(unmapped)}")
        return usage, notes

    def list_kb(self) -> KbListing:
        return KbListing(kb_dir=str(self.settings.kb_dir), files=self.kb.list())

    def read_kb(self, name: Annotated[str, Field(description="File name exactly as returned by list_kb, e.g. glossary.md.")]) -> KbDocument:
        try:
            return self.kb.read(name)
        except KbError as e:
            raise ToolError(str(e)) from e

    def get_report_map(self, report_id: Annotated[str, Field(description="Power BI report ID (GUID).")]) -> ReportEntry:
        try:
            return self.kb.report(report_id)
        except KbError as e:
            raise ToolError(str(e)) from e

    def lineage_diagram(
        self,
        table: Annotated[str, Field(description="Table as schema.table or bare name from get_model.")],
        direction: Annotated[Direction, Field(description="upstream (to bronze), downstream (to Gold) or both.")] = "upstream",
    ) -> LineageGraph:
        try:
            return table_lineage(self.index, table, direction)
        except LineageError as e:
            raise ToolError(str(e)) from e

    def report_lineage(
        self,
        report_id: Annotated[str, Field(description="Power BI report ID (GUID). Its tables are read from Power BI and mapped to Gold through the report map.")],
        workspace_id: Annotated[str | None, Field(description="Workspace ID. Omit for My workspace.")] = None,
    ) -> ReportLineage:
        try:
            entry = self.kb.report(report_id)
            md = model_metadata(self._pbi(), report_id, workspace_id)
        except (KbError, PowerBIError) as e:
            raise ToolError(str(e)) from e
        return report_lineage(self.index, entry, md.tables)

    @cached_property
    def _dbt_puller(self) -> DbtPuller:
        return DbtPuller(self.settings.dbt_repo, self.writer, mode=self.settings.dbt_pull, interval_hours=float(self.dbt_cfg.get("pull_interval_hours", 24)), timeout=float(self.dbt_cfg.get("git_timeout_seconds", 60)))

    @cached_property
    def dbt_repo(self) -> DbtRepo:
        """The local dbt clone, read once per server process after the daily pull. Raises ToolError when not configured."""
        if self.settings.dbt_repo is None:
            raise ToolError("DMA_DBT_REPO is not set. Clone the dbt repo once and point DMA_DBT_REPO at it.")
        # The pull runs before the first read, so a session sees the files of at most one day ago (D3).
        self._dbt_pull = self._dbt_puller.maybe_pull()
        try:
            return DbtRepo.load(self.settings.dbt_repo)
        except DbtRepoError as e:
            raise ToolError(str(e)) from e

    def dbt_model_lookup(self, name: Annotated[str, Field(description="Model file name, alias, or a source table name such as raw_orders. Exact match only.")]) -> DbtLookup:
        repo = self.dbt_repo
        dialect = self.dbt_cfg.get("dialect", "databricks")
        models = []
        for m, how in repo.find_models(name):
            r = repo.render(m, dialect)
            models.append(DbtModelDetail(name=m.name, match=how, path=m.path, alias=m.alias, relation=m.relation, description=m.description, columns=m.columns, raw_sql=r.raw_sql, rendered_sql=r.rendered_sql, parses=r.parses, parse_error=r.parse_error, refs=r.refs, sources=r.sources, unresolved=r.unresolved))
        if models:
            note = f"{len(models)} dbt model(s) match {name!r}. Use the logic as the Silver Staging reference; items under unresolved were not evaluated."
        else:
            note = f"No dbt model matches {name!r} by file name, alias or source table. Route it to data analysis (FR-B9). Do not pick a similar name without asking the modeller."
        return DbtLookup(query=name, exists=bool(models), route="dbt_model" if models else "data_analysis", note=note, models=models)

    def dbt_upstream(self, model_name: Annotated[str, Field(description="dbt model file name or alias (repo reader), or a manifest name, alias or unique_id (manifest fallback).")]) -> DbtUpstream:
        if self.settings.dbt_repo is not None:
            return self.dbt_repo.upstream(model_name, list(self.dbt_cfg.get("bronze_resource_types", ["source"])), self.dbt_cfg.get("dialect", "databricks"))
        if self.settings.dbt_manifest is None:
            raise ToolError("Set DMA_DBT_REPO to a local dbt clone (DMA_DBT_MANIFEST is only a fallback)")
        try:
            return DbtManifest.load(self.settings.dbt_manifest).upstream(model_name)
        except ManifestError as e:
            raise ToolError(str(e)) from e

    def dbt_repo_status(self) -> DbtRepoStatus:
        if self.settings.dbt_repo is None:
            return DbtRepoStatus(configured=False, repo=None, pull_mode=self.settings.dbt_pull)
        repo = self.dbt_repo
        return DbtRepoStatus(
            configured=True, repo=str(self.settings.dbt_repo), project=repo.name, pull_mode=self.settings.dbt_pull,
            this_session_pull=self._dbt_pull, last_logged_pull=self._dbt_puller.last(), commit=self._dbt_puller.head(),
            models=len(repo.models), sources=len(repo.sources), seeds=len(repo.seeds), problems=repo.problems,
        )

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
        bronze_tables: Annotated[list[str] | None, Field(description="Only for stage bronze_identification: the Bronze tables this version proposes, e.g. raw.raw_orders. Approving the version locks the list that data extraction may sample.")] = None,
        smx_rows: Annotated[dict[str, list[dict[str, Any]]] | None, Field(description='Build stages only: rows for the output SMX workbook as {"<sheet>": [{"<header>": value}]}, using the sheet and header names of config/smx_template.yaml. Bronze on bronze_identification, Silver - Staging on silver_staging, Silver - IDM on silver_idm, GOLD - SDM and Report Table Mapping on gold, Lookup on silver_idm or gold. Leave Status and Reviewed Date empty.')] = None,
        table_status: Annotated[dict[str, Literal["dbt_model_found", "already_modelled", "missing"]] | None, Field(description="Only for stage bronze_identification: every table the report uses, marked dbt_model_found, already_modelled or missing. Approving major gate 1 locks it.")] = None,
    ) -> StageVersion:
        try:
            return self.gate.submit(session_id, stage, content, bronze_tables, smx_rows, table_status)
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

    def grant_permission(
        self,
        session_id: Annotated[str, Field(description="Session id at stage data_extraction.")],
        permission: Annotated[Literal["data_extraction"], Field(description="Only data_extraction: sampling up to 20 rows per table.")],
        tables: Annotated[list[str], Field(description="Tables the modeller explicitly agreed to, all from the approved Bronze list.")],
    ) -> SessionView:
        try:
            return self.gate.grant(session_id, permission, tables)
        except GateError as e:
            raise ToolError(str(e)) from e

    def revoke_permission(
        self,
        session_id: Annotated[str, Field(description="Session id.")],
        permission: Annotated[Literal["data_extraction"], Field(description="The permission to end.")],
    ) -> SessionView:
        try:
            return self.gate.revoke(session_id, permission)
        except GateError as e:
            raise ToolError(str(e)) from e

    def sample_rows(
        self,
        session_id: Annotated[str, Field(description="Session id at stage data_extraction, with a logged permission.")],
        table: Annotated[str, Field(description="A granted table from the approved Bronze list.")],
        limit: Annotated[int, Field(description=f"Rows wanted, capped at {MAX_SAMPLE_ROWS}.")] = MAX_SAMPLE_ROWS,
    ) -> SampleResult:
        try:
            name = self.gate.require_sampling(session_id, table)
        except GateError as e:
            raise ToolError(str(e)) from e
        limit = min(max(int(limit), 1), MAX_SAMPLE_ROWS)
        try:
            rows = self.sampler.sample(name, limit)[:limit]
        except SamplingUnavailable as e:
            return SampleResult(session_id=session_id, table=name, limit=limit, status="not_available", reason=str(e))
        return SampleResult(session_id=session_id, table=name, limit=limit, status="ok", rows=rows)

    def choose_missing_route(
        self,
        session_id: Annotated[str, Field(description="Session id, after major gate 1 and before reuse_check is approved.")],
        table: Annotated[str, Field(description="A table marked missing in the approved bronze_identification table_status.")],
        route: Annotated[Literal["data_analysis", "provided_code"], Field(description="The modeller's explicit choice: data_analysis, or provided_code to paste dbt or SQL code.")],
    ) -> SessionView:
        try:
            return self.gate.choose_route(session_id, table, route)
        except GateError as e:
            raise ToolError(str(e)) from e

    def provide_table_code(
        self,
        session_id: Annotated[str, Field(description="Session id, after major gate 1 and before reuse_check is approved.")],
        table: Annotated[str, Field(description="A missing table whose route is provided_code.")],
        code: Annotated[str, Field(description="The dbt model or SQL the modeller pasted, exactly as given.")],
        dialect: Annotated[Literal["redshift", "databricks", "spark"], Field(description="SQL dialect of the code. dbt Jinja is handled before parsing.")] = "databricks",
    ) -> DbtLookup:
        try:
            name = self.gate.provide_code(session_id, table, code, dialect)
        except GateError as e:
            raise ToolError(str(e)) from e
        repo = self.dbt_repo if self.settings.dbt_repo is not None else DbtRepo.empty()
        r = repo.render_text(name, f"provided:{name}", code, dialect)
        detail = DbtModelDetail(name=name, match="provided", path=r.path, alias=None, relation=name, description=None, columns=[], raw_sql=r.raw_sql, rendered_sql=r.rendered_sql, parses=r.parses, parse_error=r.parse_error, refs=r.refs, sources=r.sources, unresolved=r.unresolved, bronze_tables=_provided_bronze(repo, r, dialect, list(self.dbt_cfg.get("bronze_resource_types", ["source"]))))
        note = f"Code for {name!r} is logged in the session. Its Bronze tables come from source() and resolved ref() (confirmed) or from table names in plain SQL (inferred); confirm them with the modeller."
        return DbtLookup(query=name, exists=True, origin="provided", route="provided_code", note=note, models=[detail])

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
            if view.permissions:
                md += ["", "## **3. Permission log**", "", "| # | Event | Permission | Tables | User | UTC time |", "|---|---|---|---|---|---|"]
                for i, p in enumerate(view.permissions, 1):
                    md.append(f"| {i} | {p.event} | {p.permission} | {_cell(', '.join(p.tables))} | {_cell(p.user)} | {p.ts} |")
            decisions = {
                "session_id": session_id,
                "flow": view.flow,
                "scope": view.scope,
                "approved_stages": [{"stage": o["stage"], "version": o["version"], "sha256": o["sha256"], "content": o["content"]} for o in outputs],
                "decision_log": [e.model_dump() for e in view.decision_log],
                "permission_log": [p.model_dump() for p in view.permissions],
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

    def _requirements_doc(self, session_id: str) -> tuple[str, list[str]]:
        """The approved requirements version under a header. Missing template headings are reported, never filled in."""
        outputs = {o["stage"]: o for o in self.gate.approved_outputs(session_id)}
        if REQUIREMENTS_STAGE not in outputs:
            raise ToolError(f"session {session_id} has no {REQUIREMENTS_STAGE!r} stage, so there is no requirement document to write")
        view = self.gate.view(session_id)
        stage = next(s for s in view.stages if s.stage == REQUIREMENTS_STAGE)
        o = outputs[REQUIREMENTS_STAGE]
        approval = next((e for e in reversed(view.decision_log) if e.stage == REQUIREMENTS_STAGE and e.version == o["version"] and e.decision == "approve"), None)
        header = [
            "# Report requirements", "",
            f"- Scope: {_cell(view.scope)}",
            f"- Session: `{session_id}`, stage `{REQUIREMENTS_STAGE}` version {o['version']} (sha256 `{o['sha256'][:12]}`)",
            f"- Approved by {approval.user if approval else 'unknown'} at {approval.ts if approval else 'unknown'}",
            "", "---", "",
        ]
        body = o["content"].rstrip()
        missing = [h for h in stage.headings or [] if h.lower() not in body.lower()]
        notes = [f"template headings not found in the approved requirements: {', '.join(missing)}"] if missing else []
        return "\n".join(header) + body + "\n", notes

    def write_artifact(
        self,
        kind: Annotated[ArtifactKind, Field(description="findings_md, decisions_json, lineage_mermaid, or a server-built kind with empty content: proposed_smx_xlsx (the wave3 SMX workbook from approved smx_rows, build sessions only) or requirements_md (the approved requirements stage).")],
        name: Annotated[str, Field(description="File name stem, letters, digits, '_', '-', '.' only. A timestamp prefix and extension are added.")],
        content: Annotated[str, Field(description="File content, from approved stage outputs only. JSON text for decisions_json. Empty for proposed_smx_xlsx and requirements_md.")],
        session_id: Annotated[str, Field(description="Session whose final stage is approved. Writing is refused before that.")],
    ) -> WrittenArtifact:
        try:
            self.gate.require_complete(session_id)
            notes: list[str] = []
            if kind == "requirements_md":
                if content.strip():
                    raise ToolError("requirements_md is built by the server from the approved requirements stage. Pass empty content.")
                content, notes = self._requirements_doc(session_id)
            elif kind == "proposed_smx_xlsx":
                if content.strip():
                    raise ToolError("proposed_smx_xlsx is built by the server from the approved stages' smx_rows. Pass empty content.")
                if self.gate.view(session_id).flow != "build":
                    raise ToolError("proposed_smx_xlsx is written by build sessions only; review proposals stay in the findings")
                sheets = self.gate.approved_smx_rows(session_id)
                empty = [s["name"] for s in sheets if not s["rows"]]
                notes = [f"sheets with no approved rows (header only): {', '.join(empty)}"] if empty else []
                content = json.dumps({"sheets": sheets, "style": self.gate.smx_style}, ensure_ascii=False)
            written = self.writer.write(kind, name, content)
            written.notes = notes
            self.gate.record_artifact(session_id, written.path)
            return written
        except (GateError, WriterError) as e:
            raise ToolError(str(e)) from e
