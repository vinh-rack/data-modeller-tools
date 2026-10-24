# Data Modeller Assistant

@AGENTS.md

## **1. Claude Code notes**

- Skills are authored in `skills/<name>/` and copied to `.claude/skills/` and `.kiro/skills/` by `uv run python scripts/sync_skills.py`. Edit `skills/` only, then re-run the sync. `tests/test_skills_sync.py` fails on drift.
- The project MCP server is declared in `.mcp.json` as `dma`. After changing it, approve it again in an interactive session.
- Before finishing a change, run `uv run pytest` and `uv run python scripts/smoke_client.py`.
