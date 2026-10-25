
# Data Modeller Assistant: Requirements

> 27 Oct 2026 (revised after architect review).
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

| User                         | Needs                                                                                      |
| ---------------------------- | ------------------------------------------------------------------------------------------ |
| Data modeller (primary)      | Build and self-review models faster, with existing tables surfaced before modelling starts |
| Senior reviewer or architect | Consistent findings traced to rules, plus a record of every step decision and its reason   |
| PM                           | Predictable turnaround per report, and visibility of open questions                        |

### *1.5. Scope*

- **In v1:**
  - Runs locally on each modeller's machine inside a supported IDE.
  - Reads Power BI report and semantic model metadata through the Power BI REST API, using workspace, report and dataset IDs.
  - Has ***read-only access*** to Power BI, SMX workbooks, the dbt repository and the source and target platforms.
  - A step gate (approve, disapprove, suggest changes) after every stage.
  - Artifacts are written to a local output folder.
- **Phase 0, prototype (now):** a prototype on sample data, run before v1 formally starts. It never touches client systems or data.
  - Power BI: a dummy Rackspace Power BI account with a sample report and semantic model, called through the real REST API.
  - dbt: a public GitHub repo stands in for the client dbt repo (suggested: `dbt-labs/jaffle-shop`).
  - Databricks: skipped.
  - Redshift: stubbed; source tables come from Power BI datasource info.
  - SMX: synthetic workbooks.
- **Not in v1:**
  - Full automation, or any mode that runs all stages without the modeller's decisions.
  - Hosting or a standalone application.
  - Writing to Power BI, any source system or the baseline SMX.
  - Approving models. Human reviewers keep final approval.
- **Later option:** a hosted version (see NFR-P4).

The full list of what the agent may and may not do is in ARCHITECTURE.md, Section 7 (Limitations).

## **2. Functional requirements**

### *2.1. Step gate*

Applies to every stage of Review, Build and Lineage.

| ID    | Requirement                                                                                                                   | Priority | Acceptance criteria                                                                                        |
| ----- | ----------------------------------------------------------------------------------------------------------------------------- | -------- | ---------------------------------------------------------------------------------------------------------- |
| FR-G1 | After every stage, present the output, evidence and assumptions, and offer Approve, Disapprove or Suggest changes             | Must     | No stage starts until the previous one is approved                                                         |
| FR-G2 | Approve locks the stage output                                                                                                | Must     | Later stages only use approved outputs; changing an approved stage requires the modeller to reopen it      |
| FR-G3 | Suggest changes revises only the current stage, as a new version                                                              | Must     | A comment is required; the revised version is presented at the same gate; earlier versions stay in the log |
| FR-G4 | Disapprove discards the current version and lets the modeller redo the stage, go back to an earlier stage, or end the session | Must     | A reason is required; going back invalidates later stages; nothing from a disapproved version is used      |
| FR-G5 | Log every decision: stage, version, decision, comment or reason, user, timestamp                                              | Must     | The decision log is complete and is part of the output                                                     |
| FR-G6 | Compile the final report only from approved stages plus the decision log                                                      | Must     | Disapproved and superseded versions appear in the log only, never in findings or proposed SMX rows         |
| FR-G7 | Never treat silence, an unclear answer or a partial answer as approval                                                        | Must     | The agent asks again                                                                                       |

### *2.2. Power BI integration*

| ID    | Requirement                                                                                                                                                     | Priority | Acceptance criteria                                                                             |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ----------------------------------------------------------------------------------------------- |
| FR-P1 | Identify the report by workspace ID and report ID, with dataset ID optional (replaces lookup by report name)                                                    | Must     | The report and its dataset are resolved from IDs only                                           |
| FR-P2 | Resolve the dataset from the report, and check it against a dataset ID if one is given                                                                          | Must     | A mismatch stops the session and asks the modeller; paginated reports (no dataset) are rejected |
| FR-P3 | Read report and dataset details, and the dataset's data sources                                                                                                 | Must     | Report name, dataset name and data source server and database are shown at the scope gate       |
| FR-P4 | List the semantic model's tables, columns and measures through read-only DAX queries                                                                            | Must     | Matches the model as seen in Power BI                                                           |
| FR-P5 | Retrieve Power Query (M) transforms per table, via the best available route (INFO functions, Scanner API, XMLA, or the existing source table sheet as fallback) | Should   | Every table has its M transform, or is marked "not available" with the route tried              |
| FR-P6 | Use only read scopes and metadata-sized queries; never refresh, publish or export report data                                                                   | Must     | No write call is possible from the client code                                                  |
| FR-P7 | Respect API rate limits, and cache metadata per session                                                                                                         | Should   | No rate-limit failures during a normal session                                                  |
| FR-P8 | Phase 0: run against a dummy Rackspace Power BI workspace with a sample report and semantic model                                                               | Must     | One full Build runs end to end against the dummy workspace                                      |

### *2.3. Review*

| ID     | Requirement                                                                                                                                                  | Priority | Acceptance criteria                                                                                                            |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------ |
| FR-R1  | Read every SMX workbook in a baseline folder, across the layout variants in use                                                                              | Must     | Each workbook is read correctly, with tables and columns per layer. An unrecognised layout is reported, never silently skipped |
| FR-R2  | Scope a review to one model and a chosen subset of its tables                                                                                                | Must     | Only the chosen tables are assessed                                                                                            |
| FR-R3  | Skip tables that are out of scope or already approved                                                                                                        | Should   | Skipped tables are listed with the reason                                                                                      |
| FR-R4  | Check every in-scope table against the hard rules (2.6)                                                                                                      | Must     | Every rule runs on every in-scope table, and a skipped rule is reported with the reason                                        |
| FR-R5  | Make every finding traceable                                                                                                                                 | Must     | Each finding has a rule id, severity, layer, table, optional column, message and evidence (sheet and row)                      |
| FR-R6  | Run the review in 7 stages (scope, intake, structural review, semantic review, hard-rule checklist, findings, artifacts), each ending at the step gate (2.1) | Must     | The agent stops after every stage and continues only after Approve                                                             |
| FR-R7  | Tag every statement as confirmed, inferred, assumed or unknown                                                                                               | Must     | No untagged claims about the model appear in the output                                                                        |
| FR-R8  | Produce an executive summary, severity-ranked findings, proposed changes, and decision, assumption and question logs                                         | Must     | All sections are present in the findings artifact                                                                              |
| FR-R9  | Deliver proposed changes as new artifacts only                                                                                                               | Must     | The baseline SMX is byte-identical after a session                                                                             |
| FR-R10 | Keep a resumable session record                                                                                                                              | Should   | A review can be resumed from its last approved stage                                                                           |

### *2.4. Build*

| ID    | Requirement                                                                                                                                                                    | Priority | Acceptance criteria                                                                                          |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- | ------------------------------------------------------------------------------------------------------------ |
| FR-B1 | Guide a build per Power BI report in stages: scope, Power BI intake, source tables, entity logic, bronze identification, Silver Staging, Silver IDM, Gold, rules pass, compile | Must     | Every stage ends at the step gate, and the output passes the same rules as Review                            |
| FR-B2 | Take semantic tables, columns, measures, data sources and M transforms from the Power BI integration (2.2)                                                                     | Must     | All tables and transforms for the chosen report are listed, or marked unavailable                            |
| FR-B3 | Identify the bronze tables behind each source table from the dbt entity logic (Phase 0: the GitHub stand-in repo)                                                              | Must     | Each source table maps to its bronze tables, or the agent asks when the logic is not in the repo             |
| FR-B4 | Detect reuse: show every existing table built from the same bronze table, across all SMX                                                                                       | Must     | Existing tables are listed before any new table is proposed, and only missing columns are proposed for reuse |
| FR-B5 | Check grain: the same grain in the same BU is a conflict, the same grain in another BU is a possible CDM promotion, and an aggregation of an existing fact should be a view    | Must     | Each case is flagged with the conflicting table                                                              |
| FR-B6 | Restrict Gold to the columns the report's semantic model uses                                                                                                                  | Must     | Unused Gold columns are flagged                                                                              |
| FR-B7 | Move Power Query aggregations into Gold, and keep joins that only feed visuals in Power BI                                                                                     | Should   | Each aggregation is either placed in Gold or carries an explicit reason                                      |
| FR-B8 | Write proposed SMX rows in the team's existing workbook format, from approved stages only                                                                                      | Must     | The output opens in Excel and matches the official template                                                  |

### *2.5. Lineage*

| ID    | Requirement                                                                           | Priority | Acceptance criteria                                      |
| ----- | ------------------------------------------------------------------------------------- | -------- | -------------------------------------------------------- |
| FR-L1 | List the bronze tables a dbt model depends on (manifest, or SQL parse if no manifest) | Must     | Matches the dbt dependency graph of the repo             |
| FR-L2 | Trace a SQL output column to its source columns, in Redshift and Databricks SQL       | Should   | Correct through CTEs, joins and aggregations             |
| FR-L3 | Produce table-level lineage diagrams                                                  | Must     | A diagram is saved as an artifact                        |
| FR-L4 | Produce report-level lineage (report to Gold to bronze), using Power BI API data      | Should   | Every report table traces to bronze or is marked unknown |
| FR-L5 | Run lineage on its own, or inside a build or review                                   | Should   | Available in all three flows                             |

### *2.6. Hard rules*

The rule set is shared by Build and Review. It must be deterministic, testable and configurable without code changes.

| ID      | Rule                                                                                                                                           | Priority |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| RULE-1  | Layer hierarchy: a table sources only from its own layer or upstream (Bronze, Silver Staging, Silver IDM, Gold)                                | Must     |
| RULE-2  | No layer bypass: Gold sources only Silver IDM or Gold, and Silver IDM sources only Silver Staging or Silver IDM (conventions A1.2)             | TBC (Q2) |
| RULE-3  | Gold facts hold keys and measures only, with no descriptive attributes                                                                         | Must     |
| RULE-4  | Naming: snake_case, Gold prefixes `f_`, `d_`, `agg_`, `lkp_`, view suffix `_vw`, no `_lookup` or `_enum` suffix (conventions B2) | Must     |
| RULE-5  | Every Gold column is used by the target report                                                                                                 | Must     |
| RULE-6  | One table per grain and BU, with possible CDM promotion flagged across BUs                                                                     | Must     |
| RULE-7  | A table that only aggregates an existing fact must be a view                                                                                   | Must     |
| RULE-8  | Key suffixes: `_id` for hash or surrogate keys, `_num` for natural keys (conventions B3.3)                                                 | Should   |
| RULE-9  | Metadata columns `c_created_ts`, `c_updated_ts` and `c_source_system` on Silver tables (conventions A3.1)                                | Should   |
| RULE-10 | Load behaviour documented: SCD type for dimensions, load type for facts (conventions A4.4)                                                     | Should   |
| RULE-11 | `data_area_party_id` is derived in Silver only, and Gold carries it forward 1:1                                                              | Could    |

### *2.7. Knowledge base and source access*

| ID    | Requirement                                                                                                 | Priority                     |
| ----- | ----------------------------------------------------------------------------------------------------------- | ---------------------------- |
| FR-K1 | Keep an index of every table in every SMX (bronze sources, grain, BU), rebuilt at the start of each session | Must                         |
| FR-K2 | Make the glossary, the conventions and the report-to-model map available to the agent                       | Must                         |
| FR-K3 | Read table and column metadata from Redshift (source) and Databricks (target)                               | Should (deferred in Phase 0) |
| FR-K4 | Read dbt model logic and dependencies from the dbt repository (Phase 0: GitHub stand-in)                    | Must                         |

## **3. Non-functional requirements and constraints**

### *3.1. Safety and security*

| ID     | Requirement                                                                                                                       | Priority |
| ------ | --------------------------------------------------------------------------------------------------------------------------------- | -------- |
| NFR-S1 | Read-only by design: nothing modifies Power BI, source systems or baseline SMX, and this is enforced in code, not only in prompts | Must     |
| NFR-S2 | Files are written only to a dedicated output folder, as new files. Paths that escape the folder, and overwrites, are refused      | Must     |
| NFR-S3 | No credentials in code or config files. Secrets come from environment variables                                                   | Must     |
| NFR-S4 | Every source system is accessed with read-only credentials; Power BI with read scopes only                                        | Must     |
| NFR-S5 | No real client data in the code repository. Tests use synthetic data, the dummy Power BI workspace and the public sample repo     | Must     |
| NFR-S6 | The IDE asks the modeller before any action that writes, and the final write happens only after the last gate is approved         | Must     |

### *3.2. Platform*

| ID     | Requirement                                                                                                                                                    | Priority |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| NFR-P1 | Runs locally on each modeller's machine, with reasoning by the IDE's own model and no hosting                                                                  | Must     |
| NFR-P2 | Works from Claude Code, Cursor and Kiro using one shared set of instructions and rules                                                                         | Must     |
| NFR-P3 | Can move to a hosted deployment later without rewriting tool or rule logic                                                                                     | Should   |
| NFR-P4 | Hosted option: Azure behind an Entra ID gateway. A Foundry agent client accepts only remote MCP endpoints, and non-streaming tool calls time out at 50 seconds | TBC (Q6) |
| NFR-P5 | Setup per modeller takes one install command, with no infrastructure                                                                                           | Should   |
| NFR-P6 | Connectors for Power BI, the dbt repo, Redshift and Databricks are swappable, so sample sources can be replaced by client sources through config only          | Must     |

Decided constraints: Python 3.11+ managed with `uv`, the official MCP Python SDK, and a stdio transport for v1. Library versions, API behaviour and config formats are verified against current docs at build time.

### *3.3. Quality*

| ID     | Requirement                                                                                                 | Priority |
| ------ | ----------------------------------------------------------------------------------------------------------- | -------- |
| NFR-Q1 | Deterministic: the same inputs give the same rule findings                                                  | Must     |
| NFR-Q2 | Every rule has automated tests, with at least one passing and one failing case                              | Must     |
| NFR-Q3 | SMX layout mapping and rule settings are configuration, not code                                            | Must     |
| NFR-Q4 | Auditable: every finding traces to a rule, table, column and sheet row, and every step to a logged decision | Must     |
| NFR-Q5 | The agent never fabricates. When information is missing, ambiguous or material, it stops and asks           | Must     |
| NFR-Q6 | A full review of one SMX runs fast enough to stay interactive. Target to be set in the pilot                | Should   |

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

| #   | Question                                                                                                                  | Suggested owner               | Affects              |
| --- | ------------------------------------------------------------------------------------------------------------------------- | ----------------------------- | -------------------- |
| Q1  | Can we get the official SMX template? Workbooks in use today differ in sheet names and headers                            | AGI modelling lead            | FR-R1, FR-B8         |
| Q2  | Is no-bypass (RULE-2) a hard rule, as conventions A1.2 states?                                                            | Architect                     | RULE-2               |
| Q3  | SMX has no grain or BU column. Do we define grain as the PK columns and BU from the schema name, or add explicit columns? | AGI modelling lead, Architect | RULE-6, FR-B5        |
| Q4  | If the Power BI API cannot return M transforms, what format does the existing source table sheet use as fallback?         | AGI modelling lead            | FR-P5, FR-B2, RULE-5 |
| Q5  | Where is approval status recorded per table?                                                                              | AGI modelling lead            | FR-R3                |
| Q6  | Is v1 local-only confirmed, or is hosting needed?                                                                         | PM, Architect                 | NFR-P4               |
| Q7  | When will read-only Redshift and Databricks credentials be provisioned (after Phase 0)?                                   | Client, PM                    | FR-K3                |
| Q8  | Dummy Power BI account: which license (Pro or PPU), and is the "Dataset Execute Queries REST API" tenant setting enabled? | Architect, Rackspace admin    | FR-P4, FR-P8         |
| Q9  | Does the INFO-function route (Execute DAX Queries In Group) return tables, columns and M transforms on the dummy tenant?  | AI Engineer                   | FR-P4, FR-P5         |
| Q10 | Does the datasources call need write permission on the dataset for our users?                                             | AI Engineer                   | FR-P3, NFR-S4        |
| Q11 | Which GitHub repo stands in for dbt: `dbt-labs/jaffle-shop` or another?                                                 | Architect                     | FR-B3, FR-L1         |

### *4.3. Decisions needed before build*

1. Q8 and Q11, to set up Phase 0.
2. Q1, Q3 and Q4, because they shape how models are read and what the rules check.
3. Q9 and Q10, verified on the dummy tenant in week 1.
4. Q2, to fix the final hard rule set.
5. Q6, to confirm the deployment target.

## **5. Timeline**

| ID | Phase                          | Weeks | What actually needs to be done                                                                                                                                                                                                                                                                                                                                           |
| -- | ------------------------------ | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| T1 | Discovery & convention capture | W1-W2 | Set up the dummy Power BI workspace with a sample report and model, and pick the GitHub stand-in repo.<br /><br />Test the Power BI API calls (report, dataset, datasources, DAX, M transforms) on the dummy tenant.<br /><br />Get the SMX template and agree the hard rules with AGI. Settle how grain, BU and approval status are recorded. Confirm v1 is local-only. |
| T2 | Rules engine & knowledge base  | W2-W4 | Build the Power BI API client (IDs in, metadata out), the SMX reader and the model index.<br /><br />Build the step gate and decision log. Code and test each hard rule. Load the glossary, conventions and report map. Read dependencies from the GitHub repo.<br /><br />Enforce read-only access and output-folder-only writes.                                       |
| T3 | Build capability               | W4    | Staged build per Power BI report from IDs: Power BI intake, source tables, repo logic, bronze, then Silver Staging, Silver IDM, Gold.<br /><br />Gate after every stage. Reuse and grain checks before any new table. Gold limited to the columns the report uses.<br /><br />Compile proposed SMX rows from approved stages only.                                       |
| T4 | Review capability              | W4    | 7-stage review, gated after each stage. Scope it to chosen tables and skip approved ones. Run every rule.<br /><br />Produce ranked, traceable findings plus decision, assumption and question logs.<br /><br />Never touch the baseline. Make sessions resumable from the last approved stage.                                                                          |
| T5 | Lineage capability             | W5    | Bronze dependencies from the repo. Column lineage through SQL.<br /><br />Table-level and report-level diagrams using Power BI API data, runnable on their own or inside a build or review. Databricks lineage deferred.                                                                                                                                                 |
| T6 | IDE packaging & setup guide    | W5    | One shared skill and rules pack working in Claude Code, Cursor and Kiro.<br /><br />One-command install. Setup guide per IDE, including Power BI sign-in.                                                                                                                                                                                                                |
| T7 | Testing, pilot                 | W5    | Run full Build and Review sessions on the dummy workspace with the modellers, using all three gate decisions.<br /><br />Compare against manual SMXs. Time each stage against the pitch baseline. Fix false positives.                                                                                                                                                   |
| T8 | Training & handover            | W6    | Train the modelling team. Hand over docs. Name an owner for the index, KB and rules, and plan the switch from sample sources to client sources.                                                                                                                                                                                                                          |
