---
name: dma-build
description: DRAFT. Build a new lakehouse data model for a Power BI report in 11 gated stages following the original 14-step flow (Bronze, Silver Staging, Silver IDM, Gold), with three major gates, a requirement document, and data sampling only after an explicit logged permission. Use when a data modeller asks to design or build a model for a report. Requires the dma MCP server.
metadata:
  version: "0.3-draft"
  status: draft
---

# Data model build (draft)

## **1. Ground rules**

### *1.1. Shared with the review skill*

- Follow `dma-review` section 1: deterministic checks come only from `run_rules`; read-only by design; tag every statement about the model as `[confirmed]`, `[inferred]`, `[assumed]` or `[unknown]`.
- Use the step gate exactly as `dma-review` sections 1.4 and 1.5 describe. Only an explicit Approve, Suggest changes or Disapprove is a decision. Silence, "ok" or "looks fine" means show the gate again.
- Never invent business meaning, grain, BU, DAX, M logic or profiling numbers. If a tool says "not available" or a value is missing, write `[unknown]` and ask.
- The dbt repo is reference code only. Never ask for, suggest or describe running a dbt command.

### *1.2. Session and stages*

Start with `start_session(flow="build", scope=...)` once the modeller gives the report. The server returns 11 stages with `label`, `og_step` and, for three of them, `major_gate` and `major_gate_number`. Keep the `session_id`. If you lose track, call `get_session` and follow `allowed_next`.

| # | Stage | OG step | Major gate |
|---|---|---|---|
| 1 | `scope` | | |
| 2 | `powerbi_intake` | 1 | |
| 3 | `requirements` | 2 | |
| 4 | `legacy_entities` | 3 | |
| 5 | `bronze_identification` | 4 | 1: Bronze table list |
| 6 | `data_extraction` | 5.1 | |
| 7 | `reuse_check` | 6, 7 | |
| 8 | `silver_staging` | 8 | |
| 9 | `silver_idm` | 9 | 2: Silver schemas |
| 10 | `gold` | 11, 12 | 3: Gold models |
| 11 | `rules_pass` | | |

### *1.3. Every stage output*

Each stage output, as submitted with `submit_stage` and shown to the modeller, has:

1. The output the stage asks for (section 2).
2. Evidence: tool results, file and line, Power BI IDs.
3. Assumptions and open questions.
4. A final section **Derivation and rationale**: what the output is based on (tool calls, `file:line`, approved earlier stages by name and version) and why each design choice was made.

Use only approved earlier stage versions. If an earlier stage turns out wrong, ask the modeller to Disapprove with `reopen`; never change it yourself.

### *1.4. Gate blocks*

A normal stage ends with the `dma-review` gate block, using the stage number from the table:

```
GATE <n>: <label> (version <v>)
Summary: <2 to 5 lines>
Open questions: <numbered list, or "none">
Reply with one of: Approve, Suggest changes <comment>, or Disapprove <reason>.
```

A major gate uses this block instead:

```
MAJOR GATE <major_gate_number>: <major_gate> (stage <n>, <label>, version <v>)
Approving locks: <one line on what later stages will build on>
Summary: <2 to 5 lines>
Open questions: <numbered list, or "none">
Reply with one of: Approve, Suggest changes <comment>, or Disapprove <reason>.
```

## **2. Stages**

### *2.1. Stage 1: Scope (`scope`)*

- Agree: report ID, workspace ID (leave it out for My workspace), optional dataset ID, BU, and the target SMX model if one exists.
- Tools: `list_models` if the modeller wants to see existing models.
- Output: the agreed scope, and what is out of scope. Gate 1.

### *2.2. Stage 2: Power BI intake (`powerbi_intake`, OG 1)*

- Tools: `pbi_get_report`, `pbi_model_metadata`, and `get_report_map` to see which Power BI tables already map to Gold.
- Output: report and dataset names, data sources, tables, columns with data types, measures with DAX, M per table. For anything the tools mark "not available", show the reason from `routes_tried`. Gate 2.

### *2.3. Stage 3: Requirements (`requirements`, OG 2)*

- Draft the requirement document from the approved intake only. Use the `headings` the server returned on this stage, in order (from `config/requirements_doc.yaml`).
- Report purpose comes only from what the report and model state; otherwise `[unknown]`. Measures list their DAX, or `[unknown]` if it was not readable. Tag every statement.
- This approved version is what `write_artifact(kind="requirements_md")` writes at the end. Gate 3.

### *2.4. Stage 4: Legacy entities (`legacy_entities`, OG 3)*

- Output: every semantic model table the report uses, and its source tables, taken from M and the data sources. Where M is not available, say so per table and use the source table sheet if the modeller provides it.
- Gate 4.

### *2.5. Stage 5: Bronze identification (`bronze_identification`, OG 4, MAJOR GATE 1)*

- Call `dbt_repo_status` once and show the commit and pull result.
- For each source table, call `dbt_model_lookup`. Found: show the model path, rendered SQL and `unresolved` items, then `dbt_upstream` for the `ref()` / `source()` chain to Bronze. Several matches: list them and ask which applies. Not found: "no dbt model: data analysis" for that table.
- Output: the Bronze table list with, per table, the dbt model it came from or "data analysis".
- Submit with `submit_stage(..., bronze_tables=[...])` listing exactly those Bronze tables. Approving locks that list: it is the only list data extraction may sample.
- MAJOR GATE 1: Bronze table list.

### *2.6. Stage 6: Data extraction (`data_extraction`, OG 5.1)*

1. Ask for permission in plain words, for example: "May I sample up to 20 rows from each of these Bronze tables for data analysis: <tables>? The rows are shown only here and are never saved."
2. Call `grant_permission(session_id, "data_extraction", tables)` only after an explicit yes, with only the tables the modeller agreed to. A partial yes grants only those tables. Never grant on their behalf.
3. Then call `sample_rows` per granted table. If it returns `not_available` (Phase 0 has no source connector), write the reason.
4. If the modeller declines, or sampling is not available, the output says "skipped" with the reason, and data analysis is metadata only.
5. Output: per table, what the samples show (types, formats, keys, nulls you can see in the rows), or "skipped" and why. Never compute or invent row counts, distinct counts or null rates beyond the rows shown. If the modeller withdraws permission, call `revoke_permission`.
- Gate 6. Approving this stage ends sampling for the session.

### *2.7. Stage 7: Reuse check (`reuse_check`, OG 6 and 7)*

- Tools: `find_tables_by_bronze` per Bronze table.
- Output, two lists:
  - Already modelled: the existing tables to reuse; check only missing columns.
  - New: each table and its basis: dbt logic, data analysis from samples, or metadata only.
- Gate 7.

### *2.8. Stage 8: Silver Staging (`silver_staging`, OG 8)*

- Close to Bronze with minor cleanup: renames, types, natural keys `_num`, metadata columns.
- Where a dbt model was found, follow its logic and call out every difference. Where it went to data analysis, say what the design rests on.
- Gate 8.

### *2.9. Stage 9: Silver IDM (`silver_idm`, OG 9, MAJOR GATE 2)*

- 3NF, hash keys `_id`, party key mapping. Mostly 1:1 with staging plus key mapping.
- Output: the Silver schemas (staging and IDM), table by table, with keys and sources.
- MAJOR GATE 2: Silver schemas. Approving locks the Silver layer Gold builds on.

### *2.10. Stage 10: Gold (`gold`, OG 11 and 12, MAJOR GATE 3)*

- Facts and dimensions, keys-only facts, only the columns the report uses, Power Query aggregations moved here.
- Grain check with `find_tables_by_grain` per table: same grain in another BU is a CDM promotion candidate; an aggregation of an existing fact is a view; otherwise a new table. Name the case for each table.
- MAJOR GATE 3: Gold models. Approving locks the design the rules pass checks.

### *2.11. Stage 11: Rules pass (`rules_pass`)*

- `run_rules` on the proposed tables with `report_id`, so GOLD-001 checks Gold columns against the report. Show findings as returned, plus `notes`.
- `report_lineage` for the report, with every unknown table and its reason.
- Gate 11.

## **3. After the final gate**

The server refuses these before gate 11 is approved.

1. `compile_report(session_id)`: report and decisions JSON from approved stages, the decision log and the permission log.
2. `write_artifact(session_id=..., ...)`, built from approved stages only:
   - `proposed_smx_xlsx` (OG 14): JSON `{"<sheet>": [rows]}` from the approved Silver and Gold stages. The official template is still open (Q1).
   - `requirements_md` with empty `content`: the server writes the approved `requirements` version. Show any `notes` about missing headings.
   - `lineage_mermaid`: the `mermaid` text from the approved `report_lineage` or `lineage_diagram` output, unchanged.
3. Report the returned paths. If a write is refused, show the error and ask; never retry around it.
