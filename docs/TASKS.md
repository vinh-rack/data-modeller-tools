# Data Modeller Assistant: Task List

> 30 Oct 2026. Pre-implementation plan built from the architect's "Data Modelling Assistant Agent" doc, `REQUIREMENTS.md`, `ARCHITECTURE.md` and `PHASE_0_ARCH.md`.
>
> Status: `[x]` done, `[ ]` to do. IDs in brackets point to the source (Q = open question, D = decision, FR / NFR = requirement, E = Phase 0 exit check, A = assumption in the architect's doc).

---

## **1. Scope and alignment**

- [X] Review the architect's doc together (28 Oct). Changes made: "Roadmap" renamed to "Implementation plan", placeholder removed, sample-data wording removed from the metadata-only option
- [ ] Pick a connectivity option with the client's security team: metadata-only, profiling extracts, agent in network, or Databricks as access point (A4)
- [ ] Agree the pilot set of reports and requirements (A10)

## **2. Decisions and inputs from the team**

- [X] Confirm the shared SMX files are official, output is one workbook like them (D6)
- [X] Confirm which SMX layout new builds use: `EIP_SMX_Facilities_phase2_wave3.xlsx` (Q1, A6)
- [ ] Confirm whether no-bypass (RULE-2) is a hard rule (Q2)
- [ ] Agree how grain and BU are defined: PK columns + schema name, or explicit columns (Q3)
- [X] Get the source table sheet format, as the M transform fallback: `Reports_KPI_Details.csv` (Q4, D4)
- [X] Confirm how dbt is used: reference code only, daily checkout, no dbt runs (D3)
- [ ] Ask the architect whether the `In Review/` folder is how approval status is recorded (Q5)
- [ ] Ask the architect what the requirement document should contain, and whether it is written before the final gate (Q12)
- [X] Confirm v1 is local-only (Q6)
- [ ] Get a date for read-only Redshift and Databricks credentials (Q7)
- [ ] Get the organizational standards as files the agent can read (A2)
- [ ] Get existing models in a readable form (Excel), since Erwin is out of scope (A3)
- [X] Choose the Phase 0 Power BI account: Rackspace tenant, Pro license, dedicated workspace `AGI-DMA-SANDBOX` (D1)
- [X] Choose the dbt stand-in: `dbt-labs/jaffle-shop` (D2)

## **3. Phase 0 setup**

- [X] Sign in to the Power BI REST API with `MicrosoftPowerBIMgmt`
- [X] List workspaces, reports, datasets and dashboards
- [X] Create AGI-DMA-TEST-REPORT, AGI-DMA-TEST-SM and AGI-DMA-TEST-DASHBOARD in My workspace
- [X] Find the My workspace ID (`906b05cf-...`)
- [X] Clone `dbt-labs/jaffle-shop` (read as code, no dbt install or run)
- [X] Build `AGI-DMA-TEST-JAFFLE` in Power BI Desktop from `seeds/jaffle-data/raw_*.csv`, with `stg_*` logic as Power Query steps, plus `store_targets` with no dbt model, and publish (E6)
- [X] Create workspace `AGI-DMA-SANDBOX` (`9f61964e-...`) and publish the model there, since Arrow has no My workspace path
- [X] Turn off Auto date/time in the model and republish
- [X] Delete the old AGI-DMA-TEST-* items and the My workspace copy of JAFFLE
- [X] Create synthetic SMX workbooks for testing
- [X] Keep the architect's sample pack in the git-ignored `samples/` folder, offline only
- [ ] Keep all test content in `AGI-DMA-SANDBOX`, never in shared org workspaces

## **4. Phase 0 API checks**

- [X] Get the report by ID and check it returns `datasetId` (FR-P1, E2)
- [X] Get the semantic model details (FR-P3)
- [X] Call datasources: works, but the account is admin and owner
- [ ] Test datasources with a read-only user on the sandbox (Q10, E5)
- [X] Run `EVALUATE ROW("ok", 1)` to confirm the "Dataset Execute Queries REST API" tenant setting: on (Q8)
- [X] List tables, columns and measures through DAX (FR-P4, E3)
- [X] Try Execute DAX Queries In Group with INFO functions for M transforms: works, 9 partitions with M (Q9, E4)
- [X] Test whether `groups/<My workspace ID>/...` works: only `executeQueries` does; GET and `executeDaxQueries` return 401
- [X] Check INFO.PARTITIONS on the classic endpoint: 400 even alone, so use Arrow for M
- [X] Record results in `PHASE_0_ARCH.md` and close Q8, Q9

## **5. Implementation**

### *5.1. Foundations*

- [X] Set up the MCP server (Python 3.11+, `uv`, official MCP Python SDK, stdio) with transport kept separate from tool logic (NFR-P1, NFR-P3)
- [X] Add MCP configs for Claude Code, Cursor and Kiro, with one shared skills folder (NFR-P2)
- [X] Build the artifact writer: new files in `output/` only, no overwrites, path escapes refused (NFR-S1, NFR-S2)
- [ ] Load secrets from environment variables only (NFR-S3)

### *5.2. Step gate*

- [X] Track session state with stages and versions (FR-G1)
- [X] Record Approve / Disapprove / Suggest changes with stage, version, comment or reason, user and time (FR-G2 to FR-G5)
- [X] Require a comment on Suggest changes and a reason on Disapprove (FR-G3, FR-G4)
- [X] Never treat silence or unclear answers as approval (FR-G7)
- [X] Compile the final report from approved stages only (FR-G6)
- [X] Block every write until the final gate is approved (NFR-S6)
- [X] Make sessions resumable from the last approved stage (FR-R10)

### *5.3. Knowledge base and SMX index*

- [X] Read SMX workbooks read-only, with sheet and header mapping in config (FR-R1, NFR-Q3)
- [X] Build the model index (layer, grain, BU, columns, bronze sources), rebuilt each session (FR-K1)
- [X] Load the glossary, conventions and report-to-model map (FR-K2)

### *5.4. dbt repository reader*

- [X] Keep a read-only clone and pull it at most once a day (FR-K4, D3)
- [X] Look up whether a model exists by file name or alias (FR-B3)
- [X] Follow `ref()` and `source()` statically to find upstream models and bronze tables, without running dbt or Jinja (FR-L1)
- [X] Route tables with no model to the data analysis stage (FR-B9)

### *5.5. Power BI client*

- [ ] Resolve report and dataset from IDs; stop on a dataset ID mismatch; reject paginated reports (FR-P1, FR-P2)
- [X] Read report, dataset and datasources (FR-P3)
- [X] List tables, columns and measures via read-only DAX (FR-P4)
- [ ] Get M transforms via the best route, with the source table sheet as fallback, else mark "not available" (FR-P5, D4)
- [ ] Allow read scopes only; no refresh, publish or data export (FR-P6, NFR-S4)
- [ ] Cache metadata per session and respect rate limits (FR-P7)

### *5.6. Rules engine*

- [X] Build deterministic rules RULE-1 to RULE-7, each configurable in YAML (2.6, NFR-Q1)
- [ ] Add RULE-8 to RULE-10 (Should) and RULE-11 (Could)
- [ ] Write one passing and one failing test per rule (NFR-Q2)
- [ ] Make every finding carry rule id, severity, layer, table, column and sheet row (FR-R5, NFR-Q4)

### *5.7. Build, Review and Lineage*

- [ ] Review: 7 stages with a gate after each, scoped to chosen tables, skipping approved ones (FR-R2 to FR-R9)
- [X] Build: Power BI intake from IDs, source tables, dbt model check (logic or data analysis), bronze, Silver Staging, Silver IDM, Gold, with reuse and grain checks (FR-B1 to FR-B7, FR-B9)
- [X] Align the Build stages with the original 14-step flow, gate on every stage, label the 3 major gates (D5)
- [X] Generate the requirement document per report (FR-B10)
- [X] Add the data extraction stage with separate permission, stub in Phase 0 (FR-B11)
- [X] Output the proposed model as one workbook in the SMX layout, from approved stages only (FR-B8, D6)
- [ ] Lineage: bronze dependencies from dbt, column lineage through SQL, table- and report-level diagrams (FR-L1 to FR-L5)
- [X] Tag every statement as confirmed, inferred, assumed or unknown (FR-R7, NFR-Q5)

## **6. Testing, pilot and handover**

- [X] Run one full Build end to end on `AGI-DMA-SANDBOX` using all three gate decisions (E1, FR-P8)
- [ ] Confirm nothing outside `output/` is written (E7)
- [ ] Pilot with modellers on real reports; compare with manual SMXs; time each stage against the pitch baseline (T7)
- [ ] Write the setup guide per IDE, including Power BI sign-in (T6)
- [ ] Train the modelling team and hand over docs (T8)
- [ ] Name an owner for the index, KB and rules (T8)
- [ ] Plan the switch from Phase 0 sample sources to client sources (T8)
