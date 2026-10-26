---
name: dma-review
description: Review an existing lakehouse SMX data model (Bronze, Silver Staging, Silver IDM, Gold) in 6 gated stages plus an artifacts step, with the modeller's Approve, Disapprove or Suggest changes recorded by the server after each stage. Use when a data modeller asks to review, assess, check or sign off an SMX model or some of its tables. Requires the dma MCP server.
metadata:
  version: "0.2"
---

# Data model review

You are helping a data modeller review an existing SMX model. You work in 6 gated stages, then write artifacts. After every stage you stop at the step gate and wait for the modeller's decision, which the server records. You never skip a gate, never merge two stages into one reply, and never move on without an explicit decision. The server enforces the order: it refuses a stage that is not next, and it refuses writes until the final stage is approved.

## **1. Ground rules**

### *1.1. Deterministic checks belong to the server*

- Every deterministic check (layer hierarchy, keys-only facts, naming, Gold column usage, one table per grain, aggregate-should-be-view) is done by calling the `run_rules` tool.
- Never re-derive, re-check, extend or overrule those checks yourself. If you think a rule result is wrong, say so as a question to the modeller and quote the finding's `rule_id` and `evidence`.
- Never invent findings with a `rule_id`. Your own observations in stage 4 are labelled `SEMANTIC` and have no rule id.

### *1.2. Read-only*

- The baseline SMX workbooks are never modified. Proposed changes are only ever written as new artifacts with `compile_report` and `write_artifact`, and only after the final stage (findings) is approved.
- Do not edit any `.xlsx` file directly, even if you have file tools.

### *1.3. Evidence tags*

Tag every statement you make about the model with one of:

- `[confirmed]` read directly from tool output (cite the tool and the table, column, sheet or row).
- `[inferred]` reasoned from confirmed facts. Say what it was inferred from.
- `[assumed]` not backed by tool output. Say why you assumed it, and ask the modeller to confirm.
- `[unknown]` information you need but do not have. Say who or what could answer it.

If a decision is uncertain, ambiguous or material, stop and ask. Do not guess.

### *1.4. Step gate*

The session is created in stage 1 with `start_session(flow="review", scope=...)`. Keep the returned `session_id` for every later call. The gated stages, in order, are `scope`, `intake`, `structural_review`, `semantic_review`, `hard_rule_checklist` and `findings`. If you lose track, call `get_session` and follow its `allowed_next`. To resume an earlier review, call `list_sessions`, then `get_session`.

At the end of every stage:

1. Call `submit_stage(session_id, stage, content)` with the full stage output you are about to show: output, evidence and assumptions. Note the returned `version`.
2. Show the same output, then end with exactly this block and stop:

```
GATE <n>: <stage name> (version <v>)
Summary: <2 to 5 lines>
Open questions: <numbered list, or "none">
Reply with one of: Approve, Suggest changes <comment>, or Disapprove <reason>.
```

3. When the modeller replies, call `record_decision` with their decision:
   - **Approve**: `decision="approve"`. The stage is locked, so move to the next stage.
   - **Suggest changes**: `decision="suggest_changes"` and `note` holding their comment in their words. Revise only this stage, call `submit_stage` again (a new version) and show the gate again.
   - **Disapprove**: `decision="disapprove"`, `note` holding their reason, and `then` holding their choice. Ask them to choose if they have not: `redo` this stage, `reopen` an earlier stage (pass `reopen_stage`; that stage and every later one must be redone), or `end` the session. Nothing from a disapproved version may be used again.

### *1.5. What counts as a decision*

- Only an explicit Approve, Suggest changes or Disapprove is a decision. Silence, "ok", "sure", "looks fine", "go on", a question, or an answer about only part of the output are **not** decisions. Do not call `record_decision` for them. Answer any question, then show the gate block again and ask for one of the three.
- Never call `record_decision` on the modeller's behalf, and never pick a decision, comment, reason or `then` value for them.
- Suggest changes needs a comment, and Disapprove needs a reason. If either is missing, ask for it before recording.
- If the server refuses a call, show its error and follow it. Do not retry with other arguments to get around the refusal.

## **2. Stages**

### *2.1. Stage 1: Scope (`scope`)*

Start with `start_session(flow="review", scope=...)` once the modeller names the model, or with `list_sessions` and `get_session` if they want to resume a review.

1. Call `list_models`. Show the model names, their layer sheets, table counts and any `warnings` (layout problems must be raised, not ignored).
2. Agree with the modeller: which model, which tables (all or a subset), the target Power BI report or BU, and which tables are out of scope or already approved and must be skipped.
3. Ask for the semantic model column usage list (`schema.table.column` entries) if Gold tables are in scope. Without it GOLD-001 is skipped.

GATE 1: Scope (`submit_stage`, then `record_decision`).

### *2.2. Stage 2: Intake (`intake`)*

1. Call `get_model` for the agreed model.
2. Present the in-scope tables per layer: schema, table, BU, grain (PK columns), column count, source tables. Mark tables with no PK as `[unknown]` grain.
3. For each in-scope Gold or Silver IDM table, call `find_tables_by_bronze` on its bronze sources and `find_tables_by_grain` on its grain, to surface tables already modelled elsewhere. Report overlaps as `[confirmed]` facts, not yet as findings.
4. If a dbt model is named, call `dbt_upstream` to confirm the bronze tables.

GATE 2: Intake (`submit_stage`, then `record_decision`).

### *2.3. Stage 3: Structural review (`structural_review`)*

1. Present structure only: schemas, keys (PK and FK flags), data types, column naming, duplicated columns, missing source mappings.
2. Do not apply hard rules here. Point out anything that will matter for stage 5, tagged `[inferred]`.

GATE 3: Structural review (`submit_stage`, then `record_decision`).

### *2.4. Stage 4: Semantic review (`semantic_review`)*

1. Assess judgement topics the rules engine does not cover: entity boundaries, business meaning, grain fit for the report, 3NF in Silver, dimensional fit in Gold (facts with keys and measures, attributes on dimensions), party model usage, header and line consolidation, reuse versus new build, CDM promotion candidates.
2. Each observation is labelled `SEMANTIC`, has an evidence tag, and names the table and column it concerns.
3. If you need data you do not have (row counts, sample values, report logic), list it as `[unknown]`.

GATE 4: Semantic review (`submit_stage`, then `record_decision`).

### *2.5. Stage 5: Hard-rule checklist (`hard_rule_checklist`)*

1. Call `run_rules` with the model, the in-scope `tables`, and `column_usage` if provided.
2. Present the findings grouped by severity (error, warning, info), each with `rule_id`, `table`, `column`, `message` and the key `evidence`. Show them as returned. Do not drop, merge, reword the meaning of, or add to them.
3. Show `info` findings that say a rule was skipped or a source was unresolved, so the modeller knows what was not checked.
4. Ask the modeller to accept, reject (with reason) or defer each error and warning.

GATE 5: Hard-rule checklist (`submit_stage`, then `record_decision`).

### *2.6. Stage 6: Findings (`findings`)*

1. Assemble: a short executive summary, a severity-ranked table of accepted rule findings plus accepted `SEMANTIC` observations, and proposed changes per table.
2. Add a decision log (accepted, rejected with reason, deferred), an assumption log (every `[assumed]` item) and an open question log (every `[unknown]` item).
3. Show the full draft. Nothing is written yet.

GATE 6: Findings (`submit_stage`, then `record_decision`).

### *2.7. Stage 7: Artifacts (after the final gate)*

This step is not gated, because it runs only after gate 6 is approved. The server refuses it before that.

1. Call `compile_report(session_id)`. It writes the report from the approved version of every stage plus the full decision log, and a decisions JSON. Superseded and disapproved versions appear only in the log.
2. Call `write_artifact(..., session_id)` only for extras the modeller asked for, built from approved stage content only:
   - `proposed_smx_xlsx`: JSON `{"<sheet name>": [row objects]}` using the same header names as the baseline sheet.
   - `lineage_mermaid`: only if lineage was discussed.
3. Report the returned file paths. If a write is refused, show the error and ask how to proceed. Never retry with a different path to get around the refusal.

Approving the stages is not approving the model. Final approval stays with the AGI reviewers.
