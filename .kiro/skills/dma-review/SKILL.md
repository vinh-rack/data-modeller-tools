---
name: dma-review
description: Review an existing lakehouse SMX data model (Bronze, Silver Staging, Silver IDM, Gold) in 7 staged steps with a human checkpoint after each. Use when a data modeller asks to review, assess, check or sign off an SMX model or some of its tables. Requires the dma MCP server.
metadata:
  version: "0.1"
---

# Data model review

You are helping a data modeller review an existing SMX model. You work in 7 stages. After every stage you stop at a checkpoint and wait for the modeller. You never skip a checkpoint, never merge two stages into one reply, and never move on without an explicit answer.

## **1. Ground rules**

### *1.1. Deterministic checks belong to the server*

- Every deterministic check (layer hierarchy, keys-only facts, naming, Gold column usage, one table per grain, aggregate-should-be-view) is done by calling the `run_rules` tool.
- Never re-derive, re-check, extend or overrule those checks yourself. If you think a rule result is wrong, say so as a question to the modeller and quote the finding's `rule_id` and `evidence`.
- Never invent findings with a `rule_id`. Your own observations in stage 4 are labelled `SEMANTIC` and have no rule id.

### *1.2. Read-only*

- The baseline SMX workbooks are never modified. Proposed changes are only ever written as new artifacts with `write_artifact`, and only in stage 7 after the modeller approves.
- Do not edit any `.xlsx` file directly, even if you have file tools.

### *1.3. Evidence tags*

Tag every statement you make about the model with one of:

- `[confirmed]` read directly from tool output (cite the tool and the table, column, sheet or row).
- `[inferred]` reasoned from confirmed facts. Say what it was inferred from.
- `[assumed]` not backed by tool output. Say why you assumed it, and ask the modeller to confirm.
- `[unknown]` information you need but do not have. Say who or what could answer it.

If a decision is uncertain, ambiguous or material, stop and ask. Do not guess.

### *1.4. Checkpoint format*

End every stage with exactly this block, then stop and wait:

```
CHECKPOINT <n>: <stage name>
Summary: <2 to 5 lines>
Open questions: <numbered list, or "none">
Reply with: confirm, reject <what>, or redirect <instruction>.
```

- `confirm`: move to the next stage.
- `reject`: redo the current stage with the correction, then show the checkpoint again.
- `redirect`: follow the instruction (change scope, go back to an earlier stage, stop), then show a checkpoint.

## **2. Stages**

### *2.1. Stage 1: Scope*

1. Call `list_models`. Show the model names, their layer sheets, table counts and any `warnings` (layout problems must be raised, not ignored).
2. Agree with the modeller: which model, which tables (all or a subset), the target Power BI report or BU, and which tables are out of scope or already approved and must be skipped.
3. Ask for the semantic model column usage list (`schema.table.column` entries) if Gold tables are in scope. Without it GOLD-001 is skipped.

CHECKPOINT 1: Scope.

### *2.2. Stage 2: Intake*

1. Call `get_model` for the agreed model.
2. Present the in-scope tables per layer: schema, table, BU, grain (PK columns), column count, source tables. Mark tables with no PK as `[unknown]` grain.
3. For each in-scope Gold or Silver IDM table, call `find_tables_by_bronze` on its bronze sources and `find_tables_by_grain` on its grain, to surface tables already modelled elsewhere. Report overlaps as `[confirmed]` facts, not yet as findings.
4. If a dbt model is named, call `dbt_upstream` to confirm the bronze tables.

CHECKPOINT 2: Intake.

### *2.3. Stage 3: Structural review*

1. Present structure only: schemas, keys (PK and FK flags), data types, column naming, duplicated columns, missing source mappings.
2. Do not apply hard rules here. Point out anything that will matter for stage 5, tagged `[inferred]`.

CHECKPOINT 3: Structural review.

### *2.4. Stage 4: Semantic review*

1. Assess judgement topics the rules engine does not cover: entity boundaries, business meaning, grain fit for the report, 3NF in Silver, dimensional fit in Gold (facts with keys and measures, attributes on dimensions), party model usage, header and line consolidation, reuse versus new build, CDM promotion candidates.
2. Each observation is labelled `SEMANTIC`, has an evidence tag, and names the table and column it concerns.
3. If you need data you do not have (row counts, sample values, report logic), list it as `[unknown]`.

CHECKPOINT 4: Semantic review.

### *2.5. Stage 5: Hard-rule checklist*

1. Call `run_rules` with the model, the in-scope `tables`, and `column_usage` if provided.
2. Present the findings grouped by severity (error, warning, info), each with `rule_id`, `table`, `column`, `message` and the key `evidence`. Show them as returned. Do not drop, merge, reword the meaning of, or add to them.
3. Show `info` findings that say a rule was skipped or a source was unresolved, so the modeller knows what was not checked.
4. Ask the modeller to accept, reject (with reason) or defer each error and warning.

CHECKPOINT 5: Hard-rule checklist.

### *2.6. Stage 6: Findings*

1. Assemble: a short executive summary, a severity-ranked table of accepted rule findings plus accepted `SEMANTIC` observations, and proposed changes per table.
2. Add a decision log (accepted, rejected with reason, deferred), an assumption log (every `[assumed]` item) and an open question log (every `[unknown]` item).
3. Show the full draft. Nothing is written yet.

CHECKPOINT 6: Findings.

### *2.7. Stage 7: Artifacts*

Only after checkpoint 6 is confirmed, call `write_artifact` for:

1. `findings_md`: the findings report from stage 6.
2. `decisions_json`: decisions, assumptions and open questions as JSON.
3. `proposed_smx_xlsx` only if the modeller asked for proposed rows: JSON `{"<sheet name>": [row objects]}` using the same header names as the baseline sheet.
4. `lineage_mermaid` only if lineage was discussed.

Report the returned file paths. If a write is refused, show the error and ask how to proceed. Never retry with a different path to get around the refusal.

CHECKPOINT 7: Artifacts.
