# Data Modeller Assistant: Requirements

> 2 Nov 2026 (revised after architect review; decisions D1 to D7 added, Q1, Q4, Q8 and Q9 resolved).
>
> Scope: local-first, human-gated (not fully automated). Current phase: Phase 0 (prototype, before v1 formally starts).

Sources: the pitch deck, the 17 Sept 2026 Phase 2 modelling walkthrough with AGI, AGI's formatting and naming conventions, and the 27 Oct architect review.

Priority key:

* **Must** (v1 is not usable without it)
* **Should** (expected in v1, can slip one iteration)
* **Could** (include if time allows)
* **TBC** (needs a decision first, see 4.2)

---

## **1. Background and goals**

### *1.1. Problem*

- **Slow.** Reviewing one AGI lakehouse data model (an SMX workbook) takes days of senior architect time, and reviews queue behind other work.
- **Inconsistent.** Reviewers apply the layer, key, naming and grain rules differently.
- **No record.** Why a decision was made is not written down, so the knowledge stays with a few people.
- **Reuse is checked from memory.** The hardest step is checking every existing SMX for a table with the same grain or bronze source before building a new one.

### *1.2. Goals*

Deliver an agent that runs inside the modeller's IDE, with three capabilities that share one rule set and one knowledge base:

- **Review** an existing model and produce severity-ranked, traceable findings.
- **Build** a new model layer by layer for a Power BI report, identified by its IDs and read through the Power BI REST API: Bronze, Silver Staging, Silver IDM, Gold. Silver follows 3NF (with the party model for common entities), and Gold follows dimensional modelling.
- **Trace lineage** from bronze source through the model to the report.

The agent is **not fully automated**. It prepares each step, and the modeller must **Approve**, **Disapprove** or **Suggest changes** before it continues. The final report is compiled only from approved steps. Mechanical rules are enforced in code. Judgement on grain, reuse and business meaning stays with the modeller.

### *1.3. Users*

| User                         | Needs                                                                                       |
| ---------------------------- | ------------------------------------------------------------------------------------------- |
| Data modeller (primary)      | Build and self-review models faster, with existing tables surfaced before modelling starts  |
| Senior reviewer or architect | Consistent findings traced to rules, plus a record of every step decision and its reason    |
| PM                           | Predictable turnaround per report, and visibility of open questions                         |

### *1.5. Scope*

- **In v1:**
  - Runs locally on each modeller's machine inside a supported IDE.
  - Reads Power BI report and semantic model metadata through the Power BI REST API, using workspace, report and dataset IDs.
  - Has ***read-only access*** to Power BI, SMX workbooks, the dbt repository and the source and target platforms.
  - A step gate (approve, disapprove, suggest changes) after every stage.
  - Artifacts are written to a local output folder.
- **Phase 0, prototype (now):** a prototype on sample data, run before v1 formally starts. It never touches client systems or data.
  - Power BI: a sandbox workspace in the Rackspace Inc. Power BI tenant, signed in with the AI Engineer's Pro license, holding a sample report and semantic model, called through the real REST API (decision D1).
  - dbt: the public GitHub repo `dbt-labs/jaffle-shop` stands in for the client dbt repo, read as code only (decisions D2, D3).
  - Databricks: skipped.
  - Redshift: stubbed; source tables come from Power BI datasource info.
  - SMX: synthetic workbooks.
- **Not in v1:**
  - Full automation, or any mode that runs all stages without the modeller's decisions.
  - Hosting or a standalone application.
  - Writing to Power BI, any source system or the baseline SMX.
  - Approving models. Human reviewers keep final approval.
- **Later option:** a hosted version (see NFR-P4).

The full list of what the agent may and may not do is in ARCHITECTURE.md, Section 7 (Limitations). Phase 0 setup and exit checks are in PHASE_0_ARCH.md.

## **2. Functional requirements**

### *2.1. Step gate*

Applies to every stage of Review, Build and Lineage.

| ID    | Requirement                                                                                                    | Priority | Acceptance criteria                                                                                       |
| ----- | -------------------------------------------------------------------------------------------------------------- | -------- | --------------------------------------------------------------------------------------------------------- |
| FR-G1 | After every stage, present the output, evidence and assumptions, and offer Approve, Disapprove or Suggest changes | Must     | No stage starts until the previous one is approved                                                        |
| FR-G2 | Approve locks the stage output                                                                                 | Must     | Later stages only use approved outputs; changing an approved stage requires the modeller to reopen it     |
| FR-G3 | Suggest changes revises only the current stage, as a new version                                               | Must     | A comment is required; the revised version is presented at the same gate; earlier versions stay in the log |
| FR-G4 | Disapprove discards the current version and lets the modeller redo the stage, go back to an earlier stage, or end the session | Must     | A reason is required; going back invalidates later stages; nothing from a disapproved version is used     |
| FR-G5 | Log every decision: stage, version, decision, comment or reason, user, timestamp                               | Must     | The decision log is complete and is part of the output                                                    |
| FR-G6 | Compile the final report only from approved stages plus the decision log                                       | Must     | Disapproved and superseded versions appear in the log only, never in findings or proposed SMX rows        |
| FR-G7 | Never treat silence, an unclear answer or a partial answer as approval                                         | Must     | The agent asks again                                                                                      |

### *2.2. Power BI integration*

| ID    | Requirement                                                                                                                         | Priority | Acceptance criteria                                                                                     |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------- |
| FR-P1 | Identify the report by workspace ID and report ID, with dataset ID optional (replaces lookup by report name)                        | Must     | The report and its dataset are resolved from IDs only                                                   |
| FR-P2 | Resolve the dataset from the report, and check it against a dataset ID if one is given                                              | Must     | A mismatch stops the session and asks the modeller; paginated reports (no dataset) are rejected         |
| FR-P3 | Read report and dataset details, and the dataset's data sources                                                                     | Must     | Report name, dataset name and data source server and database are shown at the scope gate               |
| FR-P4 | List the semantic model's tables, columns and measures through read-only DAX queries                                                | Must     | Matches the model as seen in Power BI                                                                   |
| FR-P5 | Retrieve Power Query (M) transforms per table, via the best available route (INFO functions, Scanner API, XMLA, or the existing source table sheet as fallback) | Should   | Every table has its M transform, or is marked "not available" with the route tried                      |
| FR-P6 | Use only read scopes and metadata-sized queries; never refresh, publish or export report data                                       | Must     | No write call is possible from the client code                                                          |
| FR-P7 | Respect API rate limits, and cache metadata per session                                                                             | Should   | No rate-limit failures during a normal session                                                          |
| FR-P8 | Phase 0: run against a sandbox Power BI workspace in the Rackspace tenant with a sample report and semantic model                              | Must     | One full Build runs end to end against the dummy workspace                                              |

### *2.3. Review*

| ID     | Requirement                                                                                                                                            | Priority | Acceptance criteria                                                                                                            |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------ |
| FR-R1  | Read every SMX workbook in a baseline folder, across the layout variants in use                                                                        | Must     | Each workbook is read correctly, with tables and columns per layer. An unrecognised layout is reported, never silently skipped |
| FR-R2  | Scope a review to one model and a chosen subset of its tables                                                                                          | Must     | Only the chosen tables are assessed                                                                                            |
| FR-R3  | Skip tables that are out of scope or already approved                                                                                                  | Should   | Skipped tables are listed with the reason                                                                                      |
| FR-R4  | Check every in-scope table against the hard rules (2.6)                                                                                                | Must     | Every rule runs on every in-scope table, and a skipped rule is reported with the reason                                        |
| FR-R5  | Make every finding traceable                                                                                                                           | Must     | Each finding has a rule id, severity, layer, table, optional column, message and evidence (sheet and row)                      |
| FR-R6  | Run the review in 7 stages (scope, intake, structural review, semantic review, hard-rule checklist, findings, artifacts), each ending at the step gate (2.1) | Must     | The agent stops after every stage and continues only after Approve                                                             |
| FR-R7  | Tag every statement as confirmed, inferred, assumed or unknown                                                                                         | Must     | No untagged claims about the model appear in the output                                                                        |
| FR-R8  | Produce an executive summary, severity-ranked findings, proposed changes, and decision, assumption and question logs                                   | Must     | All sections are present in the findings artifact                                                                              |
| FR-R9  | Deliver proposed changes as new artifacts only                                                                                                         | Must     | The baseline SMX is byte-identical after a session                                                                             |
| FR-R10 | Keep a resumable session record                                                                                                                        | Should   | A review can be resumed from its last approved stage                                                                           |

### *2.4. Build*

| ID    | Requirement                                                                                                                                                                            | Priority | Acceptance criteria                                                                                          |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------ |
| FR-B1 | Guide a build per Power BI report in 11 stages, aligned with the original 14-step flow (2 Nov): scope, Power BI intake, requirements, legacy entities, bronze identification (major gate 1), data extraction (optional), reuse check, Silver Staging, Silver IDM (major gate 2), Gold (major gate 3), rules pass. Then write the final outputs | Must | Every stage ends at the step gate. Each stage output includes its derivation and design rationale. The output passes the same rules as Review |
| FR-B2 | Take semantic tables, columns, measures, data sources and M transforms from the Power BI integration (2.2)                                                                             | Must     | All tables and transforms for the chosen report are listed, or marked unavailable                            |
| FR-B3 | Check whether a dbt model exists for each source table. If it does, use its logic and `ref()` / `source()` chain to find the bronze tables and inform Silver Staging (Phase 0: the GitHub stand-in repo)                                                                 | Must     | Each source table maps to its bronze tables, or is routed to data analysis (FR-B9) when no dbt model exists |
| FR-B4 | Detect reuse: show every existing table built from the same bronze table, across all SMX                                                                                               | Must     | Existing tables are listed before any new table is proposed, and only missing columns are proposed for reuse |
| FR-B5 | Check grain: the same grain in the same BU is a conflict, the same grain in another BU is a possible CDM promotion, and an aggregation of an existing fact should be a view            | Must     | Each case is flagged with the conflicting table                                                              |
| FR-B6 | Restrict Gold to the columns the report's semantic model uses                                                                                                                          | Must     | Unused Gold columns are flagged                                                                              |
| FR-B7 | Move Power Query aggregations into Gold, and keep joins that only feed visuals in Power BI                                                                                             | Should   | Each aggregation is either placed in Gold or carries an explicit reason                                      |
| FR-B9 | When no dbt model exists for a table, offer the modeller two ways to resolve it: (a) data analysis, where the agent proposes the logic from metadata and the samples from FR-B11 if permitted, or (b) the modeller provides their own dbt or SQL code, which is parsed like a dbt model to find its Bronze tables | Must | The bronze gate lists every table as dbt model found, already modelled, or missing with the chosen route. Analysis-based logic is approved at its gate like any other output. Provided code is logged in the session and cited as evidence |
| FR-B8 | Write the proposed model as one XLSX workbook in the same format as the shared SMX files: one sheet per layer (Bronze, Silver - Staging, Silver - IDM, GOLD - SDM) plus Lookup and Report Table Mapping, with the same headers, from approved stages only | Must | The output opens in Excel and matches the wave3 SMX layout (D6) |
| FR-B10 | Write a requirement document per report from the Power BI extraction: what the report does and which data it uses (tables, columns, measures, filters, data types, source systems) | Must | Presented at the `requirements` gate; written to `output/` with the other final outputs |
| FR-B11 | Extract up to 20 sample rows per approved Bronze table for data analysis, only after a separate, explicit permission from the modeller in that session | Should (stub in Phase 0) | Without a logged permission the sample tool refuses. If declined or not available, the stage records "skipped" with the reason and the build continues on metadata |

### *2.5. Lineage*

| ID    | Requirement                                                                     | Priority | Acceptance criteria                                      |
| ----- | ------------------------------------------------------------------------------- | -------- | -------------------------------------------------------- |
| FR-L1 | List the bronze tables a dbt model depends on, by parsing `ref()` and `source()` in the model files (no dbt run) | Must     | Matches the dependency graph of the repo                 |
| FR-L2 | Trace a SQL output column to its source columns, in Redshift and Databricks SQL | Should   | Correct through CTEs, joins and aggregations             |
| FR-L3 | Produce table-level lineage diagrams                                            | Must     | A diagram is saved as an artifact                        |
| FR-L4 | Produce report-level lineage (report to Gold to bronze), using Power BI API data | Should   | Every report table traces to bronze or is marked unknown |
| FR-L5 | Run lineage on its own, or inside a build or review                             | Should   | Available in all three flows                             |

### *2.6. Hard rules*

The rule set is shared by Build and Review. It must be deterministic, testable and configurable without code changes.

| ID      | Rule                                                                                                                                           | Priority |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| RULE-1  | Layer hierarchy: a table sources only from its own layer or upstream (Bronze, Silver Staging, Silver IDM, Gold)                                | Must     |
| RULE-2  | No layer bypass: Gold sources only Silver IDM or Gold, and Silver IDM sources only Silver Staging or Silver IDM (conventions A1.2)             | TBC (Q2) |
| RULE-3  | Gold facts hold keys and measures only, with no descriptive attributes                                                                         | Must     |
| RULE-4  | Naming: snake_case, Gold prefixes `f_`, `d_`, `agg_`, `lkp_`, view suffix `_vw`, no `_lookup` or `_enum` suffix (conventions B2)               | Must     |
| RULE-5  | Every Gold column is used by the target report                                                                                                 | Must     |
| RULE-6  | One table per grain and BU, with possible CDM promotion flagged across BUs                                                                     | Must     |
| RULE-7  | A table that only aggregates an existing fact must be a view                                                                                   | Must     |
| RULE-8  | Key suffixes: `_id` for hash or surrogate keys, `_num` for natural keys (conventions B3.3)                                                     | Should   |
| RULE-9  | Metadata columns `c_created_ts`, `c_updated_ts` and `c_source_system` on Silver tables (conventions A3.1)                                      | Should   |
| RULE-10 | Load behaviour documented: SCD type for dimensions, load type for facts (conventions A4.4)                                                     | Should   |
| RULE-11 | `data_area_party_id` is derived in Silver only, and Gold carries it forward 1:1                                                                | Could    |

### *2.7. Knowledge base and source access*

| ID    | Requirement                                                                                                 | Priority |
| ----- | ----------------------------------------------------------------------------------------------------------- | -------- |
| FR-K1 | Keep an index of every table in every SMX (bronze sources, grain, BU), rebuilt at the start of each session | Must     |
| FR-K2 | Make the glossary, the conventions and the report-to-model map available to the agent                       | Must     |
| FR-K3 | Read table and column metadata from Redshift (source) and Databricks (target)                               | Should (deferred in Phase 0) |
| FR-K4 | Keep a read-only clone of the dbt repository, pulled at most once a day, and read model logic and dependencies as code. Never run dbt commands (build, run, compile, docs generate) (D3). Phase 0: GitHub stand-in | Must     |

## **3. Non-functional requirements and constraints**

### *3.1. Safety and security*

| ID     | Requirement                                                                                                                  | Priority |
| ------ | ---------------------------------------------------------------------------------------------------------------------------- | -------- |
| NFR-S1 | Read-only by design: nothing modifies Power BI, source systems or baseline SMX, and this is enforced in code, not only in prompts | Must     |
| NFR-S2 | Files are written only to a dedicated output folder, as new files. Paths that escape the folder, and overwrites, are refused | Must     |
| NFR-S3 | No credentials in code or config files. Secrets come from environment variables                                              | Must     |
| NFR-S4 | Every source system is accessed with read-only credentials; Power BI with read scopes only                                   | Must     |
| NFR-S5 | No real client data in the code repository. Tests use synthetic data, the dummy Power BI workspace and the public sample repo | Must     |
| NFR-S6 | The IDE asks the modeller before any action that writes, and the final write happens only after the last gate is approved    | Must     |

### *3.2. Platform*

| ID     | Requirement                                                                                                                                                    | Priority |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| NFR-P1 | Runs locally on each modeller's machine, with reasoning by the IDE's own model and no hosting                                                                  | Must     |
| NFR-P2 | Works from Claude Code, Cursor and Kiro using one shared set of instructions and rules                                                                         | Must     |
| NFR-P3 | Can move to a hosted deployment later without rewriting tool or rule logic                                                                                     | Should   |
| NFR-P4 | Hosted option: Azure behind an Entra ID gateway. A Foundry agent client accepts only remote MCP endpoints, and non-streaming tool calls time out at 50 seconds | TBC (Q6) |
| NFR-P5 | Setup per modeller takes one install command, with no infrastructure                                                                                           | Should   |
| NFR-P6 | Connectors for Power BI, the dbt repo, Redshift and Databricks are swappable, so sample sources can be replaced by client sources through config only           | Must     |

Decided constraints: Python 3.11+ managed with `uv`, the official MCP Python SDK, and a stdio transport for v1. Library versions, API behaviour and config formats are verified against current docs at build time.

### *3.3. Quality*

| ID     | Requirement                                                                                       | Priority |
| ------ | ------------------------------------------------------------------------------------------------- | -------- |
| NFR-Q1 | Deterministic: the same inputs give the same rule findings                                        | Must     |
| NFR-Q2 | Every rule has automated tests, with at least one passing and one failing case                    | Must     |
| NFR-Q3 | SMX layout mapping and rule settings are configuration, not code                                  | Must     |
| NFR-Q4 | Auditable: every finding traces to a rule, table, column and sheet row, and every step to a logged decision | Must     |
| NFR-Q5 | The agent never fabricates. When information is missing, ambiguous or material, it stops and asks | Must     |
| NFR-Q6 | A full review of one SMX runs fast enough to stay interactive. Target to be set in the pilot      | Should   |

## **4. Assumptions, open questions and decisions**

### *4.1. Assumptions*

1. Modellers start a session with a Power BI workspace ID and report ID (dataset ID optional), or a usable specification (use case, domain, scope).
2. Each modeller has a licensed IDE, and client data-security policy allows the agent to be used on model metadata.
3. Power BI, the dbt repository, Redshift and Databricks are reachable from the modeller's machine, with credentials provisioned by the client. In Phase 0, only the dummy Power BI workspace and the public GitHub repo are needed.
4. The knowledge base (glossary, conventions, model index, report-to-model map) is provided as files and kept current.
5. Excel stays the canonical model artifact, organised per layer.
6. AGI's conventions and hard rules are agreed and available to encode.
7. Human reviewers keep final approval of every model and change.

### *4.2. Open questions*

| #   | Question                                                                                                                         | Suggested owner               | Affects              |
| --- | -------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- | -------------------- |
| Q1  | Resolved (2 Nov): new builds follow the layout of `EIP_SMX_Facilities_phase2_wave3.xlsx`, the most recent file (D6). Older CDM v1.0 files use a different layout and are still read, not written | AGI modelling lead | FR-R1, FR-B8 |
| Q2  | Is no-bypass (RULE-2) a hard rule, as conventions A1.2 states?                                                                   | Architect                     | RULE-2               |
| Q3  | SMX has no grain or BU column. Do we define grain as the PK columns and BU from the schema name, or add explicit columns?        | AGI modelling lead, Architect | RULE-6, FR-B5        |
| Q4  | Resolved: the source table sheet is `Reports_KPI_Details.csv` (see D4)                                                           | AGI modelling lead            | FR-P5, FR-B2, RULE-5 |
| Q5  | Where is approval status recorded per table?                                                                                     | AGI modelling lead            | FR-R3                |
| Q6  | Is v1 local-only confirmed, or is hosting needed?                                                                                | PM, Architect                 | NFR-P4               |
| Q7  | When will read-only Redshift and Databricks credentials be provisioned (after Phase 0)?                                 | Client, PM                    | FR-K3                |
| Q8  | Resolved: license is Pro (D1) and the "Dataset Execute Queries REST API" tenant setting is on (Phase 0 probe, call 4, 29 Oct)        | Architect, Rackspace admin    | FR-P4, FR-P8         |
| Q9  | Resolved: yes. Execute DAX Queries In Group (Arrow) returns tables, columns, measures with DAX and partitions with M, in a dedicated workspace on a Desktop-built model (probe call 6, 30 Oct). It has no My workspace path, and INFO.PARTITIONS fails on the classic endpoint | AI Engineer | FR-P4, FR-P5 |
| Q10 | Partly answered: the datasources call works on both paths (30 Oct), but the account is workspace admin and dataset owner, so a read-only user still needs testing | AI Engineer | FR-P3, NFR-S4 |
| Q11 | Resolved: `dbt-labs/jaffle-shop` (see D2)                                                          | Architect                     | FR-B3, FR-L1         |
| Q12 | What should the requirement document contain, and is there a template? Should it be written before the final gate? | Architect | FR-B10 |

### *4.3. Decisions needed before build*

1. Q9 is resolved and Q10 is partly answered by the Phase 0 probe; see `PHASE_0_ARCH.md` section 3.4.
2. Q1, Q3 and Q4, because they shape how models are read and what the rules check.
3. Q10, verified with a read-only user on the sandbox workspace.
4. Q2, to fix the final hard rule set.
5. Q6, to confirm the deployment target.

### *4.4. Decisions made*

Phase 0 details were left to the implementation team, so these were decided by the AI Engineer and are recorded here. They can be revisited.

| #  | Decision | Date | Consequence |
| -- | -------- | ---- | ----------- |
| D1 | Phase 0 Power BI runs in the Rackspace Inc. tenant on the AI Engineer's own account, which has a **Pro** license (confirmed from the Power BI profile). All sample content lives in one dedicated sandbox workspace | 28 Oct 2026 | XMLA is not available (needs PPU or Premium / Fabric capacity), so M transforms come from INFO.PARTITIONS on the Arrow endpoint (Q9, confirmed 30 Oct) or the source table sheet. The sandbox is a dedicated workspace, `AGI-DMA-SANDBOX`, not My workspace, because the Arrow endpoint needs a group path. The model is built in Desktop, since models created in the service from CSV are not supported. The Arrow DAX endpoint is limited to 40 requests per minute on Pro. The tenant also holds real Rackspace reports, so the agent is only given the sandbox workspace ID. A Fabric trial is offered on the account but is not used |
| D2 | `dbt-labs/jaffle-shop` is the dbt stand-in. It is cloned and read as code; its raw tables are declared as `source('ecom', ...)`, which maps cleanly to bronze. The sample Power BI model is built from its `seeds/jaffle-data` CSVs | 29 Oct 2026 (revised) | Report, model and repo line up. Bronze mapping proves the mechanism, not client correctness. `jaffle_shop_duckdb` is no longer needed |
| D3 | The dbt repository is reference code only (architect, 29 Oct). The agent checks out the code from GitHub at most once a day, checks whether a model exists, uses its logic for Silver Staging if it does, and moves to data analysis if it does not. No dbt operations are run | 29 Oct 2026 | No `manifest.json` is generated. Dependencies come from parsing `ref()` and `source()` in the model files. Jinja is not executed |
| D4 | The source table sheet is `Reports_KPI_Details.csv`, one row per semantic model table, with columns `BU`, `semantic_model`, `table_name`, `source_tables`, `select_columns`, `transformations`, `source_query`, `Soure_connection` | 29 Oct 2026 | It is the fallback route for M transforms (FR-P5) and a column usage source for RULE-5. It is AGI client metadata, so it stays in the git-ignored `samples/` folder and is never uploaded to the Power BI sandbox |
| D5 | Build stages follow the original 14-step flow (2 Nov 2026), with a gate on every stage. The original flow's 3 gates (Bronze list, Silver schemas, Gold models) are labelled major gates. The architect confirmed the number of gates can change | 2 Nov 2026 | `config/gates.yaml` build flow changes to 11 stages. Adds the requirement document (FR-B10) and sampling with separate permission (FR-B11). Sampling stays a stub until the client approves a connectivity option (A4) and Databricks access (Q7) |
| D6 | The shared SMX files are official (architect, 2 Nov). The Build output is one workbook in the same format, not one file per layer | 2 Nov 2026 | FR-B8 uses the SMX headers from config. Layout: `EIP_SMX_Facilities_phase2_wave3.xlsx`, confirmed by the architect (Q1). Header names go in config; no client rows are copied into the repo |
| D7 | Gates confirmed by the architect (2 Nov). Bronze gate: compare the report's tables against the dbt models and the existing SMX, marking each found, already modelled or missing. Silver and Gold gates: check the transform logic is correct. For missing tables, the AI Engineer chose to keep data analysis and let the modeller either approve its result or provide their own dbt or SQL code (2 Nov) | 2 Nov 2026 | Combines the architect's 29 Oct message ("move to analysis data"), his 14-step flow (step 7) and the 17 Sep recording ("ask the APJ to provide" the logic). Silver and Gold stage outputs show the rule for every column with its source |

## **5. Timeline**

| ID | Phase                          | Weeks | What actually needs to be done |
| -- | ------------------------------ | ----- | ------------------------------ |
| T1 | Discovery & convention capture | W1-W2 | Set up the sandbox Power BI workspace (Rackspace tenant, Pro) with a sample report and model built on jaffle-shop data.<br /><br />Test the Power BI API calls (report, dataset, datasources, DAX, M transforms) on the dummy tenant.<br /><br />Get the SMX template and agree the hard rules with AGI. Settle how grain, BU and approval status are recorded. Confirm v1 is local-only. |
| T2 | Rules engine & knowledge base  | W2-W4 | Build the Power BI API client (IDs in, metadata out), the SMX reader and the model index.<br /><br />Build the step gate and decision log. Code and test each hard rule. Load the glossary, conventions and report map. Read dependencies from the GitHub repo.<br /><br />Enforce read-only access and output-folder-only writes. |
| T3 | Build capability               | W4    | Staged build per Power BI report from IDs: Power BI intake, source tables, repo logic, bronze, then Silver Staging, Silver IDM, Gold.<br /><br />Gate after every stage. Reuse and grain checks before any new table. Gold limited to the columns the report uses.<br /><br />Compile proposed SMX rows from approved stages only. |
| T4 | Review capability              | W4    | 7-stage review, gated after each stage. Scope it to chosen tables and skip approved ones. Run every rule.<br /><br />Produce ranked, traceable findings plus decision, assumption and question logs.<br /><br />Never touch the baseline. Make sessions resumable from the last approved stage. |
| T5 | Lineage capability             | W5    | Bronze dependencies from the repo. Column lineage through SQL.<br /><br />Table-level and report-level diagrams using Power BI API data, runnable on their own or inside a build or review. Databricks lineage deferred. |
| T6 | IDE packaging & setup guide    | W5    | One shared skill and rules pack working in Claude Code, Cursor and Kiro.<br /><br />One-command install. Setup guide per IDE, including Power BI sign-in. |
| T7 | Testing, pilot                 | W5    | Run full Build and Review sessions on the dummy workspace with the modellers, using all three gate decisions.<br /><br />Compare against manual SMXs. Time each stage against the pitch baseline. Fix false positives. |
| T8 | Training & handover            | W6    | Train the modelling team. Hand over docs. Name an owner for the index, KB and rules, and plan the switch from sample sources to client sources. |
