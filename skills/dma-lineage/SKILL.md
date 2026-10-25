---
name: dma-lineage
description: DRAFT. Trace lineage from bronze source through Silver to Gold tables and report columns, and draw it as a Mermaid diagram. Use when a data modeller asks where a table or column comes from or what depends on a bronze table. Requires the dma MCP server.
metadata:
  version: "0.0-draft"
  status: stub
---

# Lineage (draft)

This skill is a stub for v0. Tag every statement as `[confirmed]`, `[inferred]`, `[assumed]` or `[unknown]`, and stop for confirmation before writing anything.

## **1. Steps (outline)**

1. Ask for the starting point: a bronze table, an SMX table, a dbt model or a SQL statement plus column. Checkpoint.
2. Table level: `find_tables_by_bronze` (downstream) or `get_model` source tables (upstream), and `dbt_upstream` for dbt models.
3. Column level: `sql_column_lineage` with the right dialect (`redshift` for current sources, `databricks` for the target platform).
4. Present the lineage as a table and a Mermaid `flowchart LR`. Checkpoint.
5. On approval, `write_artifact` with kind `lineage_mermaid`.
