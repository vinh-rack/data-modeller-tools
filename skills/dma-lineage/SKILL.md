---
name: dma-lineage
description: DRAFT. Trace lineage from bronze source through Silver to Gold tables and report columns, and draw it as a Mermaid diagram, with a server-enforced step gate after every stage. Use when a data modeller asks where a table or column comes from or what depends on a bronze table. Requires the dma MCP server.
metadata:
  version: "0.1-draft"
  status: stub
---

# Lineage (draft)

This skill is a stub. Tag every statement as `[confirmed]`, `[inferred]`, `[assumed]` or `[unknown]`, and use the step gate exactly as `dma-review` sections 1.4 and 1.5 describe. Start with `start_session(flow="lineage", scope=...)`. Each stage below ends with `submit_stage`, the gate block, and `record_decision` with the modeller's explicit Approve, Suggest changes or Disapprove.

## **1. Stages (outline)**

1. `scope`: the starting point, which is a bronze table, an SMX table, a Power BI report, a dbt model or a SQL statement plus column. Gate.
2. `bronze_dependencies`: `lineage_diagram` for an SMX table (`upstream`, `downstream` or `both`), `report_lineage` for a Power BI report, and `dbt_upstream` for dbt models. Report inferred nodes and unknown tables with their reasons. Gate.
3. `column_lineage`: `sql_column_lineage` with the right dialect (`redshift` for current sources, `databricks` for the target platform). If column lineage is not needed, say so as the stage output. Gate.
4. `diagram`: the lineage as a table and the `mermaid` text returned by `lineage_diagram` or `report_lineage`, unchanged. Never draw the diagram yourself. Gate.

After gate 4 is approved: `compile_report(session_id)`, then `write_artifact(kind="lineage_mermaid", ..., session_id)` with the approved diagram.
