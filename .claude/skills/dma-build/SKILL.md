---
name: dma-build
description: DRAFT. Build a new lakehouse data model for a Power BI report layer by layer (Bronze, Silver Staging, Silver IDM, Gold) with a server-enforced step gate after every stage. Use when a data modeller asks to design or build a model for a report. Requires the dma MCP server.
metadata:
  version: "0.1-draft"
  status: stub
---

# Data model build (draft)

This skill is a stub. Follow the review skill's ground rules (`dma-review`, section 1): call `run_rules` for deterministic checks, tag every statement as `[confirmed]`, `[inferred]`, `[assumed]` or `[unknown]`, and use the step gate exactly as `dma-review` sections 1.4 and 1.5 describe. Start with `start_session(flow="build", scope=...)`. Each stage below ends with `submit_stage`, the gate block, and `record_decision` with the modeller's explicit Approve, Suggest changes or Disapprove. Silence or "looks fine" is not a decision.

## **1. Stages (outline)**

1. `scope`: workspace ID, report ID, optional dataset ID, BU. Gate.
2. `powerbi_intake`: `pbi_get_report` and `pbi_model_metadata` for tables, columns, measures, data sources and M transforms, or "not available" with the route tried. Leave `workspace_id` out for My workspace. Call `get_report_map` to see which Power BI tables already map to Gold. Gate.
3. `source_tables`: source tables from the data sources and M transforms, or the source table sheet as fallback. Gate.
4. `bronze_identification`: call `dbt_repo_status` once and show the commit and pull result. Then call `dbt_model_lookup` for each source table from stage 3. The dbt repo is reference code: never ask for or suggest a dbt command.
   - Found: show the model path, its rendered SQL and its `unresolved` items (macros and Jinja that were not evaluated), call `dbt_upstream` for the `ref()` / `source()` chain to bronze, and mark that model as the Silver Staging reference `[confirmed]`. If several models match, list them and ask the modeller which one applies.
   - Not found: write "no dbt model: data analysis" for that table. In Phase 0 data analysis is metadata only, because there is no source connectivity: use the Power BI columns and data types and the SMX, say which access level was used, and never invent row counts, distinct counts, nulls or sample values. Mark what profiling would have answered as `[unknown]`.
   - Then `find_tables_by_bronze` for reuse. If a bronze table is already modelled, reuse it and check only missing columns. Gate.
5. `silver_staging`: close to bronze, minimal joins. Where stage 4 found a dbt model, follow its logic and call out every difference; where it went to data analysis, say the design rests on metadata only. Gate.
6. `silver_idm`: mostly 1:1 with staging plus key mapping (party). Grain check with `find_tables_by_grain`: same grain in another BU means flag CDM promotion; an aggregation of an existing fact means propose a view. Gate.
7. `gold`: keys-only facts, report columns only, Power Query aggregations moved here. Gate.
8. `rules_pass`: `run_rules` on the proposed tables with `report_id`, so GOLD-001 checks Gold columns against the report. Show `report_lineage` for the report, with every unknown table and its reason. Gate.

After gate 8 is approved: `compile_report(session_id)`, then `write_artifact(..., session_id)` for proposed SMX rows and lineage (`lineage_mermaid` from the approved `report_lineage` or `lineage_diagram` output), built from approved stages only.
