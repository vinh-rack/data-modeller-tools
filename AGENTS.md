# Data Modeller Assistant: project rules

These rules apply to every agent and every session working in this repo (Claude Code, Cursor, Kiro).

## **1. Hard constraints**

### *1.1. Platform*

- Python 3.11+, managed with `uv`. Run everything through `uv run` (`uv run pytest`, `uv run dma-server`).
- MCP server uses the official MCP Python SDK v2 (`mcp` package). In v2 the high-level server class is `MCPServer` (`from mcp.server import MCPServer`). `FastMCP` and `mcp.server.fastmcp` no longer exist, do not reintroduce them.
- Transport is stdio for v1 and Streamable HTTP for the hosted v2. It is chosen only in `src/dma/server.py` (`DMA_TRANSPORT`). Tool logic in `src/dma/tools.py` must never depend on the transport: who is calling comes from a resolver (`src/dma/identity.py`), never from a transport check.
- Verify current docs before using any library API or config format. Do not rely on memory. Record what was verified and where.

### *1.2. Safety*

- Read-only by design. No tool may modify source systems or baseline SMX workbooks. SMX files are opened with `openpyxl.load_workbook(read_only=True, data_only=True)` and never saved.
- Only `src/dma/writer.py` writes files, and only inside the output root (`DMA_OUTPUT_DIR`, default `output/`). The path check is enforced in code and covered by `tests/test_writer_sandbox.py`. Never weaken it.
- The writer creates three things only: new artifact files in the output root, or in `output/<owner id>/` for a verified HTTP caller (never overwritten), append-only step gate logs at `output/.sessions/<session_id>/events.jsonl` (stage versions, decisions, the `permission_granted` and `permission_revoked` events, and the `route_chosen` and `code_provided` events for missing tables), and the append-only dbt pull log at `output/.dbt/pulls.jsonl`. Log lines are never rewritten or deleted. `src/dma/gate.py` and `src/dma/lineage/dbt_pull.py` read and append through the writer and do no file IO of their own.
- Every stage passes the step gate (`config/gates.yaml`). `compile_report` and `write_artifact` are refused until the session's final stage is approved. Do not add an artifact write path that skips `GateStore.require_complete`. The two logs above are the only writes outside the gate.
- Never sample data without a logged `data_extraction` permission. The permission is the modeller's explicit yes, logged by `grant_permission`, separate from any gate decision. `sample_rows` checks it, caps rows at 20, and never writes rows to disk.
- Code the modeller pastes for a missing table (`provide_table_code`) is client code: it lives only in the session log under `output/`, never in tracked files, fixtures or tests. Tests use synthetic SQL.
- Never run dbt commands (build, run, compile, test, parse, docs generate or any other) or render Jinja from the dbt project. The dbt repo is read as files only (`src/dma/lineage/dbt_repo.py`), and `jinja2` is never imported.
- The only changes outside the output root are `git pull --ff-only` in the local dbt clone (`DMA_DBT_REPO`), at most once a day, from `src/dma/lineage/dbt_pull.py`, and in HTTP mode one `git clone` of `DMA_DBT_REPO_URL` at start when the clone is missing, from `src/dma/lineage/dbt_clone.py`. Neither ever stashes, resets or merges a dirty or diverged clone.
- No real credentials in code or config. Use env vars, keep `.env.example` as the template, `.env` stays git-ignored.
- No real client data in the repo. Tests use synthetic fixtures only (`tests/fixtures/`). `samples/` and client documents in `docs/` are git-ignored and must never be copied into tracked files.
- Never invent the SMX template layout. Sheet and header mapping lives in `config/smx_mapping.yaml`. If a layout is unknown, stop and ask.

### *1.3. Rules engine*

- Deterministic rules live in Python (`src/dma/rules/`), not in prompts. Skills tell the model to call `run_rules` and never re-derive those checks.
- Every finding has: `rule_id`, `severity`, `layer`, `table`, `column` (optional), `message`, `evidence`.
- One module per rule, each with at least one passing and one failing test. Enable rules and override severities in `config/rules.yaml`.

### *1.4. Hosted (v2)*

- No token passthrough. The MCP client's token is never sent to Power BI or any other upstream API. Entra mode gets a per-user Power BI token with the on-behalf-of flow (`src/dma/connectors/obo.py`); the client token goes to Entra only, as the OBO assertion. If OBO fails, the call stops; never fall back to `PBI_ACCESS_TOKEN` or another identity.
- In HTTP mode the gate identity comes from the verified token (`oid` plus name), through `dma.auth.token_caller`. Never take it from `DMA_USER`, a tool argument or a header. No tool takes a user argument (`tests/test_identity.py`).
- Sessions belong to the caller who started them. Every session tool and artifact resource refuses another caller's session as `unknown session`, and `list_sessions` shows only the caller's own. Add one isolation test per new session tool in `tests/test_isolation.py`.
- HTTP mode never starts without `DMA_AUTH` (`entra` or `dev`). Dev auth is for local Docker only: loopback, or `DMA_ALLOW_DEV_AUTH_ON_NETWORK=1` with the port published on `127.0.0.1`.
- Never log tokens or secrets. The log redaction in `dma.auth.install_redaction` must stay on, and error texts must not echo tokens.
- Data root (`DMA_DATA_DIR`, `/data` in the container): `output/` (artifacts, `.sessions/`, `.dbt/`), `smx/` and `kb/` (read-only mounts), and `dbt/` (the clone). The writer still writes only under `output/`.
- The dbt token (`DMA_DBT_GIT_TOKEN`) is passed per git command only. Never put it in the remote URL, `.git/config`, argv, `os.environ`, logs or the image.
- Gate operations that replay and append run under the per-session lock (`ArtifactWriter.session_lock`). One replica is the supported setup.
- Never bake secrets or client data into the image. `.dockerignore` stays an allowlist; secrets come from the environment at run time.

## **2. Formatting**

- No em dashes, en dashes, arrow characters or any character not on a standard laptop keyboard, in code, comments or docs. Use `->` style ASCII only where code syntax needs it.
- Do not align `=` or `:` with padding. Do not wrap lines to fit a UI box, break lines only when necessary.
- Markdown headings: `# Heading 1` plain, `## **1. Heading 2**` bold and numbered, `### *1.1. Heading 3*` italic and sub numbered. Italics use `*`, not `_`. Keep Heading 2s below 5, minimise Heading 4, little to no emojis.

## **3. Working style**

- Small commits, one concern each.
- If something is ambiguous (SMX layout, naming prefixes, grain wording), stop and ask instead of guessing.
- Tag statements about the data model as confirmed, inferred, assumed or unknown.
