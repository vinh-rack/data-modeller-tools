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

The smoke client starts `dma-server` over stdio, lists the 21 tools, runs `run_rules` on the synthetic fixture model, and runs one gated session to final approval in a temp folder. To check the server on its own, run `uv run dma-server`. It waits silently on stdin, so press Ctrl+C to stop it.

To see what every tool does, run `uv run python scripts/tool_gallery.py`. It calls the SMX, knowledge base, lineage and gate tools on the fixtures (the tools that read Power BI need a live token, so they are left out) and writes `output/tool_gallery.html`, showing each tool's input, a readable view of its output and the raw JSON.

Optional: the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) needs Node 22.19 or newer. In its UI, choose a stdio connection with command `uv` and arguments `run --quiet dma-server`, started from the repo root.

### *1.3. Environment variables*

Copy `.env.example` to `.env` and edit it. `.env` is git-ignored, and the server loads it from its working directory on start. Relative paths resolve against the repo root.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DMA_SMX_DIR` | `tests/fixtures/smx` | Folder with the baseline SMX workbooks. Opened read-only. |
| `DMA_DBT_MANIFEST` | unset | Path to a dbt `manifest.json` (schema v12), used by `dbt_upstream`. |
| `DMA_OUTPUT_DIR` | `output` | The only folder the server writes to: artifacts, plus gate logs in `.sessions/`. It must not be inside `DMA_SMX_DIR`. |
| `DMA_CONFIG_DIR` | `config` | Folder holding `smx_mapping.yaml`, `rules.yaml` and `gates.yaml`. |
| `DMA_KB_DIR` | `kb` | Knowledge base folder: glossary, conventions and `report_map.yaml`. The repo copy holds templates only, so point this at a git-ignored folder (for example `samples/kb`) for client files. |
| `DMA_USER` | OS user | Name recorded on every gate decision. It is an audit label, not authentication. |
| `DMA_TRANSPORT` | `stdio` | Only `stdio` is enabled in v1. |
| `PBI_ACCESS_TOKEN` | empty | Power BI bearer token, with or without the `Bearer ` prefix. See 3.4. Never commit it. |
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

- MCP: `.kiro/settings/mcp.json`. Kiro starts servers with the first workspace root as the working directory, so open the repo folder as the first root. The read-only tools are auto-approved. The tools that write (the gate tools, `compile_report` and `write_artifact`) still ask for approval, which gives the modeller a second click on every decision.
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
| `dbt_upstream` | no | Lists the bronze sources of a dbt model from the manifest |
| `sql_column_lineage` | no | Traces a SQL output column to its source columns (SQLGlot) |
| `lineage_diagram` | no | Builds table-level lineage from the SMX index, upstream, downstream or both, with a Mermaid flowchart |
| `report_lineage` | no | Traces each Power BI table of a report through its Gold table to Bronze, or marks it unknown with the reason |
| `list_kb` | no | Lists the knowledge base files |
| `read_kb` | no | Reads one knowledge base file by its exact listed name |
| `get_report_map` | no | Returns a report's SMX model and the Gold table behind each Power BI table |
| `pbi_get_report` | no | Reads a Power BI report, its semantic model and its data sources by ID |
| `pbi_model_metadata` | no | Lists the semantic model's tables, columns, measures and M transforms, with the routes tried |
| `start_session` | gate log | Starts a gated `review`, `build` or `lineage` session and returns its stages |
| `submit_stage` | gate log | Records the next stage's output as a new version |
| `record_decision` | gate log | Records `approve`, `suggest_changes` (comment) or `disapprove` (reason, then `redo`, `reopen` or `end`) |
| `get_session` | no | Shows the current stage, versions, decision log and what is allowed next |
| `list_sessions` | no | Lists gate sessions so one can be resumed |
| `compile_report` | `output/` only | After final approval, writes the report and decisions files from approved stages plus the decision log |
| `write_artifact` | `output/` only | After final approval, writes `findings_md`, `decisions_json`, `proposed_smx_xlsx` or `lineage_mermaid` as a new file |

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

`config/smx_mapping.yaml` maps SMX sheet names and header aliases to fields. It is a placeholder built from the observed layouts, so confirm it against the official SMX template.

### *3.2. Knowledge base and lineage*

`kb/report_map.yaml` maps a Power BI report ID to its SMX model and the Gold table behind each Power BI table, with optional column renames. `run_rules` with `report_id` reads the report's columns from Power BI and maps them to Gold for GOLD-001, so nobody types a usage list. `report_lineage` uses the same map to go from the report to Gold to Bronze. A Power BI table that is not in the map is reported as unknown, never guessed.

`lineage_diagram` and `report_lineage` build the Mermaid text in code. Nodes are confirmed when the table is in the SMX index and inferred when it is only named in a source cell. An unqualified source name is matched only in its own layer, because names like `vehicle_master` exist in several layers.

### *3.3. Step gate*

The stage lists for each flow live in `config/gates.yaml`. A session keeps the list it started with.

- Stages run strictly in order. Only the stage after the last approved one can be submitted, and only the latest version waiting for a decision can be decided.
- Approve locks the stage. Suggest changes needs a comment and opens a new version. Disapprove needs a reason plus `redo`, `reopen` an earlier stage (which invalidates it and every later stage), or `end`.
- `compile_report` and `write_artifact` are refused until the final stage is approved. The report uses approved versions only; superseded and disapproved versions appear only in the decision log.
- Every event goes to `output/.sessions/<session_id>/events.jsonl`, append-only, with the user and a UTC timestamp. State is rebuilt from that file, so sessions survive a server restart. Use `list_sessions` and `get_session` to resume.
- The server cannot see the modeller. The skills tell the IDE model to pass only the modeller's explicit word, and the server refuses anything other than the three decision values.

### *3.4. Power BI*

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
