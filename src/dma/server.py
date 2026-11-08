"""MCP entry point: builds the server, registers tools, picks the transport. No tool logic here.

The transport is chosen here only (DMA_TRANSPORT): stdio for v1, or Streamable HTTP for the hosted v2. Tool logic in
tools.py is the same for both.
"""

import logging
import os
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from dma.config import HttpSettings, Settings, load_settings
from dma.tools import ARTIFACT_URI, WORKBOOK_URI, DmaTools
from dma.writer import XLSX_MIME

SERVER_NAME = "data-modeller-assistant"


def _version() -> str:
    try:
        return package_version(SERVER_NAME)
    except PackageNotFoundError:
        return "unknown"

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
# Sampling reads source data once a connector exists; Phase 0 has none.
READS_SOURCE = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
READS_POWER_BI = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
WRITES_OUTPUT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)

INSTRUCTIONS = (
    "Read-only assistant for lakehouse SMX data models (Bronze, Silver Staging, Silver IDM, Gold). "
    "Use run_rules for every deterministic check instead of re-deriving it. "
    "Every stage goes through the step gate: submit_stage, show the output, then record_decision with the modeller's exact word "
    "(approve, suggest_changes or disapprove). Silence or vague replies are not decisions: ask again. "
    "compile_report and write_artifact only work after the final stage is approved, and only write into the output folder."
)

TOOLS = [
    ("list_models", "List SMX models in the baseline folder with layer sheets, table counts and layout warnings. Start here.", READ_ONLY),
    ("get_model", "Return every table of one SMX model: layer, schema, BU, grain (PK columns), columns and source tables.", READ_ONLY),
    ("find_tables_by_bronze", "Find modelled tables in any SMX that source from a bronze table, directly or through upstream layers. Use for reuse checks.", READ_ONLY),
    ("find_tables_by_grain", "Find tables in any SMX whose grain (PK column set) equals the given columns. Use to check one table per grain.", READ_ONLY),
    ("run_rules", "Run the deterministic rules (LAYER-001, LAYER-002 when enabled, FACT-001, NAME-001, GOLD-001, KEY-001, META-001, PARTY-001, GRAIN-001, GRAIN-002) on a model or chosen tables and return findings with evidence. Pass report_id so GOLD-001 checks Gold columns against the report.", READ_ONLY),
    ("list_kb", "List the knowledge base files (glossary, conventions, report-to-model map) in DMA_KB_DIR.", READ_ONLY),
    ("read_kb", "Read one knowledge base file by the exact name list_kb returned. Use it to explain findings, never to re-derive run_rules checks.", READ_ONLY),
    ("get_report_map", "Return the report-to-model map entry for a Power BI report: its SMX model and the Gold table behind each Power BI table.", READ_ONLY),
    ("lineage_diagram", "Build table-level lineage from the SMX index: nodes tagged confirmed or inferred, edges, and a Mermaid flowchart to show the modeller and later save with write_artifact (lineage_mermaid).", READ_ONLY),
    ("report_lineage", "Trace each Power BI table of a report to its Gold table and Bronze sources, using Power BI metadata, the report map and the SMX index. Unmapped tables are marked unknown. Returns a Mermaid flowchart too.", READS_POWER_BI),
    ("dbt_model_lookup", "Check whether a dbt model exists in the local dbt clone, by file name, alias or source table. Returns its raw and rendered SQL, refs, sources and unresolved Jinja, or route data_analysis when none exists. dbt is never run.", READ_ONLY),
    ("dbt_upstream", "List the bronze tables a dbt model depends on by following ref() and source() in the local dbt clone (no dbt run), with file:line evidence. Falls back to a manifest only when DMA_DBT_REPO is not set.", READ_ONLY),
    ("dbt_repo_status", "Show the local dbt clone: path, this session's pull result, the last logged pull, the current commit, and model, source and seed counts.", READ_ONLY),
    ("sql_column_lineage", "Trace one output column of a SQL statement to its source table columns (redshift, databricks or spark dialect).", READ_ONLY),
    ("pbi_get_report", "Read a Power BI report, its semantic model and its data sources by ID (read-only). Rejects paginated reports and a dataset ID that does not match the report.", READS_POWER_BI),
    ("pbi_model_metadata", "List the semantic model's tables, columns, measures and Power Query (M) transforms by report ID, with the routes tried. Unavailable parts are marked, not guessed.", READS_POWER_BI),
    ("start_session", "Start a gated review, build or lineage session. Returns the session id and its ordered stages. Call once at the start of a playbook.", WRITES_OUTPUT),
    ("submit_stage", "Record the output of the next stage as a new version before showing it to the modeller. Only the stage after the last approved one is accepted.", WRITES_OUTPUT),
    ("record_decision", "Record the modeller's explicit decision on a stage version: approve, suggest_changes (comment required) or disapprove (reason and redo, reopen or end required).", WRITES_OUTPUT),
    ("get_session", "Show a session: current stage, every version and its status, the decision log and what is allowed next. Use to resume.", READ_ONLY),
    ("list_sessions", "List gate sessions in the output folder with flow, scope, status and current stage.", READ_ONLY),
    ("grant_permission", "Log the modeller's explicit permission to sample data (data_extraction) for listed tables from the approved Bronze list. Only at stage data_extraction, never on the modeller's behalf.", WRITES_OUTPUT),
    ("revoke_permission", "Log the end of a data_extraction permission. Sampling stops at once.", WRITES_OUTPUT),
    ("sample_rows", "Return up to 20 sample rows of one granted Bronze table for the data_extraction stage. Refused without a logged permission. Phase 0 has no source connector, so it returns not_available. Rows are never written to disk.", READS_SOURCE),
    ("choose_missing_route", "Log the modeller's explicit route for a table marked missing at major gate 1: data_analysis, or provided_code to paste code. Only after major gate 1 and before reuse_check is approved.", WRITES_OUTPUT),
    ("provide_table_code", "Log dbt or SQL code the modeller pasted for a missing table with route provided_code, and parse it like a dbt model (refs, sources, Jinja stripped, unresolved recorded) to find its Bronze tables. Returns the dbt_model_lookup shape with origin provided.", WRITES_OUTPUT),
    ("compile_report", "After the final stage is approved, write the report and decisions artifacts from approved stage outputs plus the full decision log.", WRITES_OUTPUT),
    ("write_artifact", "Write a new artifact into the output folder for a session whose final stage is approved. Never overwrites and never writes anywhere else.", WRITES_OUTPUT),
]


def build_server(tools: DmaTools | None = None, **auth: Any) -> MCPServer:
    """The MCP server with every tool. auth (token_verifier and auth settings) is given only in HTTP mode."""
    tools = tools or DmaTools(load_settings())
    srv = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version=_version(), **auth)
    for name, description, annotations in TOOLS:
        srv.tool(name=name, description=description, annotations=annotations)(getattr(tools, name))

    # The outputs of a session whose final stage is approved, for clients that cannot open the output folder.
    # write_artifact and compile_report return each file's URI. The owner check is the same as for every session tool.
    @srv.resource(ARTIFACT_URI, name="artifact", description="A text artifact (report, decisions, requirements, lineage) of one of your sessions.", mime_type="text/plain")
    def _artifact(session_id: str, file_name: str) -> str:
        return tools.read_artifact(session_id, file_name, binary=False)

    @srv.resource(WORKBOOK_URI, name="workbook", description="The proposed SMX workbook of one of your build sessions.", mime_type=XLSX_MIME)
    def _workbook(session_id: str, file_name: str) -> bytes:
        return tools.read_artifact(session_id, file_name, binary=True)

    health = {"status": "ok", "version": _version(), "commit": os.environ.get("DMA_GIT_COMMIT", "").strip() or "unknown"}

    # Unauthenticated by design (custom routes skip the bearer check). Static: it never touches Power BI, dbt or disk.
    @srv.custom_route("/health", methods=["GET"])
    async def _health(request: Request) -> JSONResponse:
        return JSONResponse(health)

    return srv


def http_app(srv: MCPServer, http: HttpSettings) -> Starlette:
    """The Streamable HTTP ASGI app. Stateless, because gate state is replayed from disk on every call and no tool
    sends server-to-client requests. DNS rebinding protection is always on, also when bound to 0.0.0.0."""
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=list(http.allowed_hosts), allowed_origins=list(http.allowed_origins))
    return srv.streamable_http_app(streamable_http_path=http.path, stateless_http=True, json_response=True, transport_security=security, host=http.host)


def serve_http(app: Starlette, http: HttpSettings) -> None:
    """Run uvicorn directly: MCPServer.run() does not pass proxy settings. Trusted proxy IPs come from uvicorn's own
    FORWARDED_ALLOW_IPS (default 127.0.0.1 and ::1)."""
    import uvicorn

    config = uvicorn.Config(app, host=http.host, port=http.port, proxy_headers=http.proxy_headers, server_header=False, log_level="info")
    uvicorn.Server(config).run()


def build_http(settings: Settings, env: Mapping[str, str], jwks_client: Any = None, obo_app: Any = None, pbi_send: Any = None) -> Starlette:
    """The authenticated HTTP app. The caller of each request comes from its verified token, sessions are refused to
    anyone but their owner, and artifacts go to output/<owner>/. Raises AuthConfigError when auth is not set up.

    Power BI: entra mode gets a per-user token by on-behalf-of and never forwards the client's token; dev mode keeps
    PBI_ACCESS_TOKEN. jwks_client, obo_app and pbi_send replace the network calls in tests."""
    from dma.auth import build_auth, install_redaction, load_auth, token_caller
    from dma.connectors.powerbi import PowerBIClient

    install_redaction(env)
    cfg = load_auth(env, settings.http)
    if cfg.mode == "entra":
        from dma.connectors.obo import OboTokenProvider, client_factory, load_obo

        pbi_factory = client_factory(OboTokenProvider(load_obo(env, cfg.tenant_id, cfg.client_id), app=obo_app), env, send=pbi_send)
    else:
        pbi_factory = lambda: PowerBIClient.from_env(env, send=pbi_send)
    tools = DmaTools(settings, pbi_factory=pbi_factory, caller=token_caller, enforce_owner=True)
    return http_app(build_server(tools, **build_auth(cfg, jwks_client)), settings.http)


def main() -> None:
    load_dotenv(Path.cwd() / ".env")
    # stdout carries the stdio protocol, so logs go to stderr only.
    logging.basicConfig(level=logging.WARNING)
    settings = load_settings()
    if settings.transport == "stdio":
        build_server(DmaTools(settings)).run(transport="stdio")
        return
    from dma.auth import AuthConfigError

    logging.getLogger("dma").setLevel(logging.INFO)
    # HTTP serves a shared server, so it never starts without authentication.
    try:
        app = build_http(settings, os.environ)
    except AuthConfigError as e:
        raise SystemExit(str(e)) from None
    serve_http(app, settings.http)


if __name__ == "__main__":
    main()
