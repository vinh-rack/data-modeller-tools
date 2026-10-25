---
name: dma-build
description: DRAFT. Build a new lakehouse data model for a Power BI report layer by layer (Bronze, Silver Staging, Silver IDM, Gold) with a checkpoint at every layer. Use when a data modeller asks to design or build a model for a report. Requires the dma MCP server.
metadata:
  version: "0.0-draft"
  status: stub
---

# Data model build (draft)

This skill is a stub for v0. Follow the review skill's ground rules (`dma-review`, section 1): call `run_rules` for deterministic checks, tag every statement as `[confirmed]`, `[inferred]`, `[assumed]` or `[unknown]`, and stop at a checkpoint after every step.

## **1. Steps (outline)**

1. Scope: report ID, BU, in-scope semantic model tables. Checkpoint.
2. Semantic model intake and source tables from the source table sheet. Checkpoint.
3. Bronze identification: `dbt_upstream` on the entity model, then `find_tables_by_bronze` for reuse. If a bronze table is already modelled, reuse it and check only missing columns. Checkpoint.
4. Silver Staging close to bronze, minimal joins. Checkpoint.
5. Silver IDM, mostly 1:1 with staging plus key mapping (party). Checkpoint.
6. Grain check with `find_tables_by_grain`: same grain in another BU means flag CDM promotion; an aggregation of an existing fact means propose a view. Checkpoint.
7. Gold: keys-only facts, report columns only, Power Query aggregations moved here. Checkpoint.
8. `run_rules` on the proposed tables, then `write_artifact` for proposed SMX rows, decision log and lineage. Checkpoint.
