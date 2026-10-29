"""MCP entry point: builds the server, registers tools, picks the transport. No tool logic here."""

import logging
from pathlib import Path

from dotenv import load_dotenv
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from dma.config import load_settings
from dma.tools import DmaTools

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
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
    ("dbt_upstream", "List the bronze source tables a dbt model depends on, from the dbt manifest depends_on graph.", READ_ONLY),
    ("sql_column_lineage", "Trace one output column of a SQL statement to its source table columns (redshift, databricks or spark dialect).", READ_ONLY),
    ("pbi_get_report", "Read a Power BI report, its semantic model and its data sources by ID (read-only). Rejects paginated reports and a dataset ID that does not match the report.", READS_POWER_BI),
    ("pbi_model_metadata", "List the semantic model's tables, columns, measures and Power Query (M) transforms by report ID, with the routes tried. Unavailable parts are marked, not guessed.", READS_POWER_BI),
    ("start_session","Start a gated review, build or lineage session. Returns the session id and its ordered stages. Call once at the start of a playbook.", WRITES_OUTPUT),
    ("submit_stage", "Record the output of the next stage as a new version before showing it to the modeller. Only the stage after the last approved one is accepted.", WRITES_OUTPUT),
    ("record_decision", "Record the modeller's explicit decision on a stage version: approve, suggest_changes (comment required) or disapprove (reason and redo, reopen or end required).", WRITES_OUTPUT),
    ("get_session", "Show a session: current stage, every version and its status, the decision log and what is allowed next. Use to resume.", READ_ONLY),
    ("list_sessions", "List gate sessions in the output folder with flow, scope, status and current stage.", READ_ONLY),
    ("compile_report", "After the final stage is approved, write the report and decisions artifacts from approved stage outputs plus the full decision log.", WRITES_OUTPUT),
    ("write_artifact", "Write a new artifact into the output folder for a session whose final stage is approved. Never overwrites and never writes anywhere else.", WRITES_OUTPUT),
]


def build_server(tools: DmaTools | None = None) -> MCPServer:
    tools = tools or DmaTools(load_settings())
    srv = MCPServer("data-modeller-assistant", instructions=INSTRUCTIONS, version="0.1.0")
    for name, description, annotations in TOOLS:
        srv.tool(name=name, description=description, annotations=annotations)(getattr(tools, name))
    return srv


def main() -> None:
    load_dotenv(Path.cwd() / ".env")
    # stdout carries the stdio protocol, so logs go to stderr only.
    logging.basicConfig(level=logging.WARNING)
    settings = load_settings()
    srv = build_server(DmaTools(settings))
    if settings.transport == "stdio":
        srv.run(transport="stdio")
    else:
        # v2: add "streamable-http" here with srv.run(transport="streamable-http", host=..., port=...). Tools stay unchanged.
        raise SystemExit(f"DMA_TRANSPORT={settings.transport!r} is not enabled in v1. Use stdio.")


if __name__ == "__main__":
    main()
