# Data Modeller Assistant

A local, read-only MCP server plus an instruction pack (skills) that helps data modellers review, build and trace lineage for lakehouse data models: Bronze, Silver Staging, Silver IDM and Gold. Deterministic rules run in Python. The IDE's own model handles judgement and explanation, and stops at a human checkpoint after every stage.

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

The smoke client starts `dma-server` over stdio, lists the 8 tools and runs `run_rules` on the synthetic fixture model. To check the server on its own, run `uv run dma-server`. It waits silently on stdin, so press Ctrl+C to stop it.

Optional: the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) needs Node 22.19 or newer. In its UI, choose a stdio connection with command `uv` and arguments `run --quiet dma-server`, started from the repo root.

### *1.3. Environment variables*

Copy `.env.example` to `.env` and edit it. `.env` is git-ignored, and the server loads it from its working directory on start. Relative paths resolve against the repo root.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DMA_SMX_DIR` | `tests/fixtures/smx` | Folder with the baseline SMX workbooks. Opened read-only. |
| `DMA_DBT_MANIFEST` | unset | Path to a dbt `manifest.json` (schema v12), used by `dbt_upstream`. |
| `DMA_OUTPUT_DIR` | `output` | The only folder `write_artifact` may write to. It must not be inside `DMA_SMX_DIR`. |
| `DMA_CONFIG_DIR` | `config` | Folder holding `smx_mapping.yaml` and `rules.yaml`. |
| `DMA_TRANSPORT` | `stdio` | Only `stdio` is enabled in v1. |
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

- MCP: `.kiro/settings/mcp.json`. Kiro starts servers with the first workspace root as the working directory, so open the repo folder as the first root. The read-only tools are auto-approved, and `write_artifact` still asks for approval.
- Skills: loaded from `.kiro/skills/`, which is a copy.
- Project rules: `AGENTS.md`.

### *2.4. Keeping skills in sync*

Edit skills only in `skills/<name>/SKILL.md`, then run:

```bash
uv run python scripts/sync_skills.py
```

This copies them into `.claude/skills/` and `.kiro/skills/`. `tests/test_skills_sync.py` fails if a copy drifts. Copies are used instead of symlinks because symlinks on Windows need Developer Mode, and Kiro does not document symlink support.

## **3. Tools and rules**

| Tool | Writes | What it does |
| --- | --- | --- |
| `list_models` | no | Lists the SMX models with their layer sheets, table counts and layout warnings |
| `get_model` | no | Returns the tables of one model with layer, BU, grain, columns and sources |
| `find_tables_by_bronze` | no | Finds tables that source from a bronze table, including transitively |
| `find_tables_by_grain` | no | Finds tables with the same grain (PK column set) |
| `run_rules` | no | Runs the deterministic rules and returns findings |
| `dbt_upstream` | no | Lists the bronze sources of a dbt model from the manifest |
| `sql_column_lineage` | no | Traces a SQL output column to its source columns (SQLGlot) |
| `write_artifact` | `output/` only | Writes `findings_md`, `decisions_json`, `proposed_smx_xlsx` or `lineage_mermaid` as a new file |

Every finding has `rule_id`, `severity`, `layer`, `table`, `column`, `message` and `evidence`. The v0 rules are LAYER-001, FACT-001, NAME-001, GOLD-001, GRAIN-001 and GRAIN-002. They are configured in `config/rules.yaml`.

`config/smx_mapping.yaml` maps SMX sheet names and header aliases to fields. It is a placeholder built from the observed layouts, so confirm it against the official SMX template.

## **4. Adding a rule**

1. Create `src/dma/rules/<topic>.py` with a function decorated `@rule("ABC-001")`. It takes `(ctx, cfg)` and returns `list[Finding]`. `ctx.targets` holds the tables under review, `ctx.index` holds every indexed table, and `cfg` is the rule's block in `rules.yaml`.
2. Import the module in `src/dma/rules/__init__.py` so it registers.
3. Add an `ABC-001` block to `config/rules.yaml` with `enabled`, `severity` and any thresholds. Keep every tunable value in YAML, not code.
4. Add tests to `tests/test_rules.py`, with at least one case that passes and one that fails. Then run `uv run pytest`.
5. Mention the new rule id in the `run_rules` description in `src/dma/server.py`.
