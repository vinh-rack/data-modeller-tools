# Data Modeller Assistant

A local, read-only MCP server plus an instruction pack (skills) that helps data modellers review, build and trace lineage for lakehouse data models: Bronze, Silver Staging, Silver IDM and Gold. Deterministic rules run in Python. The IDE's own model handles judgement and explanation. Every stage ends at a step gate enforced by the server: the modeller must Approve, Disapprove or Suggest changes before the next stage starts, and nothing is written until the final stage is approved.

## **1. Setup**

### *1.1. Requirements*

- Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/) on your `PATH`.
- One of Claude Code, Cursor or Kiro.

### *1.2. Install and check*

```bash
uv sync
uv run pytest
uv run python scripts/smoke_client.py
```

The smoke client starts `dma-server` over stdio, lists the 28 tools, runs `run_rules` on the synthetic fixture model, and runs one 11-stage build session to final approval in a temp folder, with sampling permission declined and one missing table per route, then writes the XLSX, the requirement document and the lineage. To check the server on its own, run `uv run dma-server`. It waits silently on stdin, so press Ctrl+C to stop it.

To see what every tool does, run `uv run python scripts/tool_gallery.py`. It calls the SMX, knowledge base, lineage and gate tools on the fixtures (the tools that read Power BI need a live token, so they are left out) and writes `output/tool_gallery.html`, showing each tool's input, a readable view of its output and the raw JSON.

Optional: the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) needs Node 22.19 or newer. In its UI, choose a stdio connection with command `uv` and arguments `run --quiet dma-server`, started from the repo root.

### *1.3. Environment variables*

Copy `.env.example` to `.env` and edit it. `.env` is git-ignored, and the server loads it from its working directory on start. Relative paths resolve against the repo root.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DMA_SMX_DIR` | `tests/fixtures/smx` | Folder with the baseline SMX workbooks. Opened read-only. |
| `DMA_DBT_REPO` | unset | Local clone of the dbt repo, read as code only. See 3.3. |
| `DMA_DBT_PULL` | `auto` | `auto` runs `git pull --ff-only` in `DMA_DBT_REPO` at most once a day; `off` never pulls. |
| `DMA_DBT_MANIFEST` | unset | Fallback only: a dbt `manifest.json` (schema v12) for `dbt_upstream` when `DMA_DBT_REPO` is not set. |
| `DMA_OUTPUT_DIR` | `output` | The only folder the server writes to: artifacts, plus gate logs in `.sessions/`. It must not be inside `DMA_SMX_DIR`. |
| `DMA_CONFIG_DIR` | `config` | Folder holding `smx_mapping.yaml`, `rules.yaml` and `gates.yaml`. |
| `DMA_KB_DIR` | `kb` | Knowledge base folder: glossary, conventions and `report_map.yaml`. The repo copy holds templates only, so point this at a git-ignored folder (for example `samples/kb`) for client files. |
| `DMA_USER` | OS user | Name recorded on every gate decision. It is an audit label, not authentication. |
| `DMA_TRANSPORT` | `stdio` | Only `stdio` is enabled in v1. |
| `PBI_ACCESS_TOKEN` | empty | Power BI bearer token, with or without the `Bearer ` prefix. See 3.5. Never commit it. |
| `PBI_API_BASE` | `https://api.powerbi.com/v1.0/myorg` | Power BI REST base URL. Must be https. |
| `PBI_MAX_ROWS` | `5000` | Row cap for metadata DAX queries. |
| `PBI_REPORT_ID`, `PBI_DATASET_ID`, `PBI_WORKSPACE_ID` | empty | Used only by `scripts/pbi_probe.py`. Leave the workspace empty for My workspace. |
| `REDSHIFT_*`, `DATABRICKS_*` | empty | Reserved for the metadata connectors, which are stubs in v0. Use read-only credentials. |

Never put real credentials in the JSON configs or commit them.

## **2. Connect from your IDE**

Every IDE starts the same command, `uv run dma-server`, from the repo root. The configs are already in the repo.

### *2.1. Claude Code*

- MCP: `.mcp.json` at the repo root (project scope). Start `claude` in the repo and approve the `dma` server when prompted. Check it with `claude mcp list`.
- Skills: loaded from `.claude/skills/` (`/dma-review`, `/dma-build`, `/dma-lineage`).
- Project rules: `CLAUDE.md`, which imports `AGENTS.md`.

### *2.2. Cursor*

- MCP: `.cursor/mcp.json`. It runs `uv run --directory ${workspaceFolder} dma-server`. Enable `dma` under Settings, MCP.
- Skills: Cursor reads `.claude/skills/` through its compatibility path, so no extra copy is needed.
- Project rules: `AGENTS.md`.

### *2.3. Kiro*

- MCP: `.kiro/settings/mcp.json`. Kiro starts servers with the first workspace root as the working directory, so open the repo folder as the first root. The read-only tools are auto-approved. The tools that write (the gate and permission tools, `compile_report` and `write_artifact`) and `sample_rows` still ask for approval, which gives the modeller a second click on every decision.
- Skills: loaded from `.kiro/skills/`, which is a copy.
- Project rules: `AGENTS.md`.

### *2.4. Keeping skills in sync*

Edit skills only in `skills/<name>/SKILL.md`, then run:

```bash
uv run python scripts/sync_skills.py
```

This copies them into `.claude/skills/` and `.kiro/skills/`. `tests/test_skills_sync.py` fails if a copy drifts. Copies are used instead of symlinks because symlinks on Windows need Developer Mode, and Kiro does not document symlink support.

## **3. Tools and rules**

### *3.1. Tools*

| Tool | Writes | What it does |
| --- | --- | --- |
| `list_models` | no | Lists the SMX models with their layer sheets, table counts and layout warnings |
| `get_model` | no | Returns the tables of one model with layer, BU, grain, columns and sources |
| `find_tables_by_bronze` | no | Finds tables that source from a bronze table, including transitively |
| `find_tables_by_grain` | no | Finds tables with the same grain (PK column set) |
| `run_rules` | no | Runs the deterministic rules and returns findings |
| `dbt_model_lookup` | no | Says whether a dbt model exists by file name, alias or source table, with its raw and rendered SQL, refs, sources and unresolved Jinja |
| `dbt_upstream` | no | Lists the bronze tables of a dbt model by following `ref()` and `source()` in the clone, with `file:line` evidence |
| `dbt_repo_status` | no | Shows the clone path, the pull result, the commit and the model, source and seed counts |
| `sql_column_lineage` | no | Traces a SQL output column to its source columns (SQLGlot) |
| `lineage_diagram` | no | Builds table-level lineage from the SMX index, upstream, downstream or both, with a Mermaid flowchart |
| `report_lineage` | no | Traces each Power BI table of a report through its Gold table to Bronze, or marks it unknown with the reason |
| `list_kb` | no | Lists the knowledge base files |
| `read_kb` | no | Reads one knowledge base file by its exact listed name |
| `get_report_map` | no | Returns a report's SMX model and the Gold table behind each Power BI table |
| `pbi_get_report` | no | Reads a Power BI report, its semantic model and its data sources by ID |
| `pbi_model_metadata` | no | Lists the semantic model's tables, columns, measures and M transforms, with the routes tried |
| `start_session` | gate log | Starts a gated `review`, `build` or `lineage` session and returns its stages |
| `submit_stage` | gate log | Records the next stage's output as a new version. On `bronze_identification` it also takes the `bronze_tables` list and the `table_status` per report table, and on the build design stages the `smx_rows` for the workbook |
| `record_decision` | gate log | Records `approve`, `suggest_changes` (comment) or `disapprove` (reason, then `redo`, `reopen` or `end`) |
| `get_session` | no | Shows the current stage, versions, decision log and what is allowed next |
| `list_sessions` | no | Lists gate sessions so one can be resumed |
| `grant_permission` | gate log | Logs the modeller's `data_extraction` permission for tables from the approved Bronze list, only at stage `data_extraction` |
| `revoke_permission` | gate log | Logs the end of that permission; sampling stops at once |
| `choose_missing_route` | gate log | Logs the modeller's route for a missing table: `data_analysis` or `provided_code` |
| `provide_table_code` | gate log | Logs the dbt or SQL code pasted for a missing table and parses it like a dbt model, returning its Bronze tables |
| `sample_rows` | no | Up to 20 rows of a granted Bronze table, only with a logged permission. A stub in Phase 0: returns `not_available` |
| `compile_report` | `output/` only | After final approval, writes the report and decisions files from approved stages plus the decision and permission logs |
| `write_artifact` | `output/` only | After final approval, writes `findings_md`, `decisions_json` or `lineage_mermaid`, or the server-built `proposed_smx_xlsx` (build only) and `requirements_md`, as a new file |

Every finding has `rule_id`, `severity`, `layer`, `table`, `column`, `message` and `evidence`. The rules are configured in `config/rules.yaml`:

| Rule id | Requirement | Checks |
| --- | --- | --- |
| LAYER-001 | RULE-1 | A table sources only from its own layer or upstream |
| LAYER-002 | RULE-2 | No layer bypass. Off by default until Q2 is answered |
| FACT-001 | RULE-3 | Gold facts hold keys and measures only |
| NAME-001 | RULE-4 | snake_case, Gold prefixes, `_vw` suffix, no `_lookup` or `_enum` |
| GOLD-001 | RULE-5 | Every Gold column is used by the report. Pass `report_id` to `run_rules` |
| GRAIN-001 | RULE-6 | One table per grain and BU, CDM promotion across BUs |
| GRAIN-002 | RULE-7 | An aggregate of an existing fact should be a view |
| KEY-001 | RULE-8 | `_num` natural keys, `_id` hash keys, with the key type inferred from the layer and hash() rules |
| META-001 | RULE-9 | `c_created_ts`, `c_updated_ts` and `c_source_system` on Silver |
| PARTY-001 | RULE-11 | Gold carries `data_area_party_id` forward 1:1 |

RULE-10 (load behaviour documented) is not built: the SMX layouts have no SCD or load type column to check, so it waits for the official template (Q1).

`config/smx_mapping.yaml` maps SMX sheet names and header aliases to fields for reading. It reads the wave3 layout, the older CDM v1.0 layout (DocInfo, Bronze - Landing, Lookups, Silver- Staging) and the canonical A2 layout, because Review and the reuse check open existing workbooks in all of them.

### *3.2. Knowledge base and lineage*

`kb/report_map.yaml` maps a Power BI report ID to its SMX model and the Gold table behind each Power BI table, with optional column renames. `run_rules` with `report_id` reads the report's columns from Power BI and maps them to Gold for GOLD-001, so nobody types a usage list. `report_lineage` uses the same map to go from the report to Gold to Bronze. A Power BI table that is not in the map is reported as unknown, never guessed.

`lineage_diagram` and `report_lineage` build the Mermaid text in code. Nodes are confirmed when the table is in the SMX index and inferred when it is only named in a source cell. An unqualified source name is matched only in its own layer, because names like `vehicle_master` exist in several layers.

### *3.3. dbt repository*

dbt is reference code only (REQUIREMENTS D3). The server never runs a dbt command and never renders Jinja, so there is no `manifest.json`. Clone the repo once yourself, outside this repo, and point `DMA_DBT_REPO` at it. For Phase 0:

```bash
git clone https://github.com/dbt-labs/jaffle-shop.git ../jaffle-shop
```

Then set `DMA_DBT_REPO=../jaffle-shop` in `.env`. On the first dbt tool call of a session, the server runs `git pull --ff-only` in the clone if the last pull is older than 24 hours, and records each attempt in `output/.dbt/pulls.jsonl`. A dirty or diverged clone, missing git or no network is reported by `dbt_repo_status` and the current files are used; the server never stashes, resets or re-clones.

How the reader works:

- It reads `dbt_project.yml` and the model-path YAML with `yaml.safe_load`, and every model `.sql`, source and seed as files. Sources use dbt's defaults: the schema is the source name and the identifier is the table name unless set.
- It finds `ref('m')`, `ref('pkg', 'm')`, `ref('m', v=2)` and `source('s', 't')` in the raw SQL. Only literal arguments are read.
- For SQLGlot it replaces refs with the model alias or name, and sources with `schema.identifier`. It drops comments, `config()` and `{% if is_incremental() %}` bodies, strips other `{% %}` tags, and replaces any other `{{ }}` (macros, `var()`, `this`) with a numbered placeholder. Every one of these is listed under `unresolved` with its file and line.
- A name with no exact match returns `exists: false` and route `data_analysis` (FR-B9). There is no fuzzy matching.

Limits: refs are not schema-qualified, because the schema depends on the target and the project's `generate_schema_name`. `+alias` in `dbt_project.yml` and installed packages (`dbt_packages/`) are not read. Seeds count as bronze only when `config/dbt.yaml` lists `seed` in `bronze_resource_types`.

### *3.4. Step gate*

The stage lists for each flow live in `config/gates.yaml`. A session keeps the list it started with.

- Stages run strictly in order. Only the stage after the last approved one can be submitted, and only the latest version waiting for a decision can be decided.
- Approve locks the stage. Suggest changes needs a comment and opens a new version. Disapprove needs a reason plus `redo`, `reopen` an earlier stage (which invalidates it and every later stage), or `end`.
- `compile_report` and `write_artifact` are refused until the final stage is approved. The report uses approved versions only; superseded and disapproved versions appear only in the decision log.
- Every event goes to `output/.sessions/<session_id>/events.jsonl`, append-only, with the user and a UTC timestamp. State is rebuilt from that file, so sessions survive a server restart. Use `list_sessions` and `get_session` to resume.
- The server cannot see the modeller. The skills tell the IDE model to pass only the modeller's explicit word, and the server refuses anything other than the three decision values.

The build flow follows the original 14-step flow of 2 Nov (REQUIREMENTS D5). Every stage has a gate; its three named gates are labelled major gates, and `start_session` and `get_session` return each stage's `label`, `og_step` and `major_gate`.

| # | Stage | OG step | Major gate |
| --- | --- | --- | --- |
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

Build sessions started before this change keep their 8 stages and still run.

**Gate meanings (D7).** Major gate 1 compares every table the report uses against the dbt models and the existing SMX and puts it in one group: `dbt_model_found`, `already_modelled` or `missing` (`submit_stage(..., table_status={...})`). Major gates 2 and 3 check the transform logic: every Silver Staging, Silver IDM and Gold row in `smx_rows` must fill its rule and source columns, or the submit is refused.

**Missing tables (FR-B9).** After major gate 1 and before the reuse check is approved, the modeller picks a route per missing table, logged by `choose_missing_route`:

- `data_analysis`: the agent proposes the logic from metadata, plus `sample_rows` results only if data extraction was permitted, tagged `[inferred]`. No profiling numbers are invented.
- `provided_code`: the modeller pastes dbt or SQL code, logged by `provide_table_code` with its hash. It is parsed with the dbt reader (refs, sources, Jinja stripped, unresolved recorded) and SQLGlot. Refs and sources that resolve in `DMA_DBT_REPO` give confirmed Bronze tables; tables named in plain SQL are inferred.

The reuse check is refused while a missing table has no route, or has `provided_code` without code. The reuse check, Silver and Gold outputs must cite each missing table as `<table> (route: data_analysis)` or `<table> (route: provided)`. `get_session` lists the missing tables with their route and whether code was given. A reopen of `bronze_identification` clears the routes. Pasted code stays in the session log only.

**Data extraction (FR-B11).** The approved `bronze_identification` version carries the Bronze table list (`submit_stage(..., bronze_tables=[...])`). At `data_extraction` the modeller may give a separate permission, logged by `grant_permission` with the user, time and tables. `sample_rows` is refused without an active permission, after `revoke_permission`, for a table that was not granted or is no longer in the approved Bronze list, and once `data_extraction` is approved. It caps rows at 20 and never writes them to disk. In Phase 0 there is no source connector, so it returns `not_available`; a future connector implements `RowSampler` in `src/dma/connectors/sampling.py`. If the permission is declined or sampling is not available, the stage output says "skipped" with the reason and is approved like any other.

**SMX workbook (FR-B8, D6).** A build writes one workbook per model, in the layout of `EIP_SMX_Facilities_phase2_wave3.xlsx` (Q1 resolved). `config/smx_template.yaml` holds the sheets and header names, in this order:

| Sheet | Rows come from | Notes |
| --- | --- | --- |
| Bronze | `bronze_identification` | |
| Silver - Staging | `silver_staging` | |
| Silver - IDM | `silver_idm` | |
| GOLD - SDM | `gold` | |
| Lookup | `silver_idm`, `gold` | |
| Report Table Mapping | `gold` | `Status` and `Reviewed Date` always empty: approval stays with AGI reviewers |

The stages pass their rows as `submit_stage(..., smx_rows={"<sheet>": [{"<header>": value}]})`, checked against the template. After the final gate, `write_artifact(kind="proposed_smx_xlsx", content="")` writes all six sheets from the approved versions only, with a header row on sheets that got no rows. The team tracking columns (PIC, Updated By, Updated Date, Development Planned Date, Expected Completion Date, ETL POC) are left out, and the older CDM v1.0 layout is never written. The workbook follows the look of the shared SMX files (Calibri, bold header on amber, thin borders, filter), with a frozen header, columns sized to their content, tabs coloured by layer and the reviewer-only columns shaded. The style lives under `style` in `config/smx_template.yaml`. Review sessions keep proposed changes in their findings.

**Requirement document (FR-B10).** The `requirements` stage is drafted with the headings in `config/requirements_doc.yaml`, which can change without code changes while Q12 is open. After the final gate, `write_artifact(kind="requirements_md", content="")` writes the approved `requirements` version under a header with the session, version and approver, and lists any template headings missing from it.

### *3.5. Power BI*

The client is read-only by construction. Every request is checked against an allowlist before it is sent: GET on report, dataset and datasource metadata, and POST only to the two DAX query endpoints. DAX must be a plain `EVALUATE` query, and results are capped at `PBI_MAX_ROWS`. With no workspace ID the My workspace paths are used, and with one the `groups/{id}/` paths.

Get a token (Phase 0 uses your own delegated sign-in). Install the module once in an elevated PowerShell session; Microsoft requires version 1.3.84 or later to sign in.

```powershell
Install-Module -Name MicrosoftPowerBIMgmt
Connect-PowerBIServiceAccount
Get-PowerBIAccessToken -AsString
```

Paste the whole output into `PBI_ACCESS_TOKEN` in `.env`, then reconnect the `dma` server. The token is never logged and is redacted from errors. When it expires, the tools return a 401 message telling you to refresh it. Sign-in through MSAL is a TODO.

`pbi_model_metadata` tries INFO functions through Execute DAX Queries In Group first, which needs a workspace ID and the `pbi-arrow` extra (pyarrow) to read its Arrow response. Otherwise it runs the INFO.VIEW queries one at a time on the classic endpoint. The docs say INFO functions are not supported there, but the Phase 0 probe showed INFO.VIEW working. If that fails too, it falls back to column names from COLUMNSTATISTICS. Any part it cannot get is marked not available, with the reason in `routes_tried`.

For content in My workspace, leave the workspace ID out. In Phase 0 the GET and Execute DAX Queries calls on `groups/{My workspace ID}/` returned 401 while the same token passed on the My workspace paths, so the Arrow route is not available there. Only the classic executeQueries call worked on that group path.

Run the Phase 0 probe against your sandbox IDs (set `PBI_REPORT_ID`, `PBI_DATASET_ID` and optionally `PBI_WORKSPACE_ID` in `.env`):

```bash
uv run --extra pbi-arrow python scripts/pbi_probe.py
```

It runs the calls from the ARCHITECTURE call plan plus checks of INFO.VIEW and INFO.PARTITIONS on the classic endpoint, prints PASS, FAIL or SKIP for each, and answers Q8 (tenant setting), Q9 (INFO route), Q10 (datasources permission) and whether group paths work for My workspace. It makes no writes to Power BI. pytest never makes live calls.

## **4. Adding a rule**

1. Create `src/dma/rules/<topic>.py` with a function decorated `@rule("ABC-001")`. It takes `(ctx, cfg)` and returns `list[Finding]`. `ctx.targets` holds the tables under review, `ctx.index` holds every indexed table, and `cfg` is the rule's block in `rules.yaml`.
2. Import the module in `src/dma/rules/__init__.py` so it registers.
3. Add an `ABC-001` block to `config/rules.yaml` with `enabled`, `severity` and any thresholds. Keep every tunable value in YAML, not code.
4. Add tests to `tests/test_rules.py`, with at least one case that passes and one that fails. Then run `uv run pytest`.
5. Mention the new rule id in the `run_rules` description in `src/dma/server.py`.
