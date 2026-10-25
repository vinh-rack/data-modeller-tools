"""Call every MCP tool on the synthetic fixtures and render one static HTML page showing input and output.

Run: uv run python scripts/tool_gallery.py
Writes output/tool_gallery.html. Demo writes from write_artifact go to a temp folder, not output/.
"""

import asyncio
import html
import json
import sys
import tempfile
from pathlib import Path

from mcp import Client

from dma.config import load_settings
from dma.server import build_server
from dma.tools import DmaTools
from dma.writer import ArtifactWriter

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
LAYERS = ["bronze", "silver_staging", "silver_idm", "gold"]

CALLS = [
    ("list_models", {}),
    ("get_model", {"model_name": "synthetic_logistics_canonical"}),
    ("find_tables_by_bronze", {"bronze_table": "brnz_fleetops.booking"}),
    ("find_tables_by_grain", {"grain_text": "vehicle_id"}),
    ("run_rules", {"model_name": "synthetic_fleet_eip"}),
    ("dbt_upstream", {"model_name": "fct_booking"}),
    ("sql_column_lineage", {"sql": (FIX / "sql" / "fct_booking.sql").read_text(encoding="utf-8"), "column": "total_amount", "dialect": "databricks"}),
    ("write_artifact", {"kind": "findings_md", "name": "../escape", "content": "should be refused"}),
    ("write_artifact", {"kind": "findings_md", "name": "review", "content": "# Findings\n"}),
]

CSS = """
:root{--bg:#fafafa;--fg:#1d1d1f;--muted:#6e6e73;--card:#fff;--line:#e3e3e8;--chip:#f1f1f4;--err:#c62828;--warn:#b26a00;--info:#1565c0;--ok:#2e7d32}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececf0;--muted:#9a9aa2;--card:#1d1d20;--line:#2e2e33;--chip:#26262b;--err:#ef6b6b;--warn:#f0a940;--info:#6ea8ff;--ok:#6fcf73}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{margin:0 0 4px}p.sub{color:var(--muted);margin:0 0 24px}
nav{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:24px}nav a{background:var(--chip);color:var(--fg);padding:4px 10px;border-radius:12px;text-decoration:none;font-size:13px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:18px}
h2{margin:0;font-size:17px}.tag{font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;vertical-align:middle;border:1px solid currentColor}
.ro{color:var(--ok)}.rw{color:var(--warn)}.desc{color:var(--muted);margin:4px 0 12px}
code,pre{font:12.5px/1.45 ui-monospace,Consolas,monospace}pre{background:var(--chip);padding:10px;border-radius:6px;overflow:auto;margin:6px 0}
.label{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-top:12px}
table{border-collapse:collapse;width:100%;margin-top:6px}th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600;font-size:12px}
.wrap{overflow-x:auto}.sev{font-weight:600}.error{color:var(--err)}.warning{color:var(--warn)}.info{color:var(--info)}
.flow{display:flex;gap:10px;overflow-x:auto;padding:4px 0}.col{min-width:170px;flex:1}.col h4{margin:0 0 6px;font-size:12px;color:var(--muted);text-transform:uppercase}
.box{background:var(--chip);border-left:3px solid var(--info);border-radius:4px;padding:5px 8px;margin-bottom:6px;font:12.5px ui-monospace,Consolas,monospace}
.col+.col{border-left:1px dashed var(--line);padding-left:10px}.box.hl{border-left-color:var(--warn)}
.banner{padding:8px 12px;border-radius:6px;margin-top:6px;border:1px solid currentColor}
details summary{cursor:pointer;color:var(--muted);font-size:12px;margin-top:10px}
"""


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def table(headers, rows) -> str:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="wrap"><table><tr>{head}</tr>{body}</table></div>'


def flow(columns) -> str:
    cols = []
    for title, items in columns:
        boxes = "".join(f'<div class="box{" hl" if hl else ""}">{esc(text)}</div>' for text, hl in items) or '<div class="desc">none</div>'
        cols.append(f'<div class="col"><h4>{esc(title)}</h4>{boxes}</div>')
    return f'<div class="flow">{"".join(cols)}</div>'


def render_view(name, args, data, error) -> str:
    if error:
        return f'<div class="banner error">Refused: {esc(error)}</div>'
    if name == "list_models":
        return table(["model", "layer sheets", "tables", "warnings"], [
            (f"<code>{esc(m['name'])}</code>", esc(", ".join(f"{k} ({v})" for k, v in m["sheets"].items())), m["table_count"], esc("; ".join(m["warnings"]) or "none"))
            for m in data["models"]])
    if name == "get_model":
        return table(["layer", "table", "BU", "grain", "columns", "sources"], [
            (esc(t["layer"]), f"<code>{esc(t['qualified'])}</code>", esc(t["bu"] or "-"), esc(", ".join(t["grain"]) or "-"), len(t["columns"]), esc(", ".join(s["qualified"] for s in t["sources"])))
            for t in data["tables"]])
    if name == "find_tables_by_bronze":
        by_layer = {l: [(t["table"], False) for t in data["tables"] if t["layer"] == l] for l in LAYERS}
        by_layer["bronze"] = [(args["bronze_table"], True)]
        return flow([(l.replace("_", " "), by_layer[l]) for l in LAYERS])
    if name == "find_tables_by_grain":
        return table(["table", "layer", "BU", "model"], [
            (f"<code>{esc(t['table'])}</code>", esc(t["layer"]), esc(t["bu"] or "-"), esc(t["model"])) for t in data["tables"]])
    if name == "run_rules":
        counts = " ".join(f'<span class="sev {k}">{v} {k}</span>' for k, v in data["counts"].items())
        rows = [
            (f'<span class="sev {f["severity"]}">{esc(f["severity"])}</span>', f"<code>{esc(f['rule_id'])}</code>", f"<code>{esc(f['table'])}</code>", esc(f["column"] or ""), esc(f["message"]),
             "<code>" + esc(", ".join(f"{k}={v}" for k, v in f["evidence"].items())) + "</code>")
            for f in data["findings"]]
        return f"<div>{len(data['tables_checked'])} tables checked: {counts}</div>" + table(["severity", "rule", "table", "column", "message", "evidence"], rows)
    if name == "dbt_upstream":
        return flow([
            ("bronze sources", [(b["table"], True) for b in data["bronze_tables"]]),
            ("via models", [(v.split(".")[-1], False) for v in data["via_models"]]),
            ("model", [(data["unique_id"].split(".")[-1], False)]),
        ]) + (f'<div class="banner warning">Missing nodes: {esc(", ".join(data["missing_nodes"]))}</div>' if data["missing_nodes"] else "")
    if name == "sql_column_lineage":
        return f'<div class="label">SQL</div><pre>{esc(args["sql"])}</pre>' + flow([
            ("source columns", [(f"{s['table']}.{s['column']}", True) for s in data["sources"]]),
            ("output column", [(data["column"], False)]),
        ])
    if name == "write_artifact":
        return f'<div class="banner ok">Written: <code>{esc(Path(data["path"]).name)}</code> ({data["bytes"]} bytes) inside the output folder</div>'
    return ""


def section(i, name, desc, read_only, args, data, error) -> str:
    shown_args = {k: (v if k != "sql" else "(see SQL below)") for k, v in args.items()}
    tag = '<span class="tag ro">read-only</span>' if read_only else '<span class="tag rw">writes output/ only</span>'
    raw = esc(json.dumps(data if data is not None else {"error": error}, indent=2))
    return (f'<section id="t{i}"><h2><code>{esc(name)}</code>{tag}</h2><div class="desc">{esc(desc)}</div>'
            f'<div class="label">Input</div><pre>{esc(json.dumps(shown_args, indent=2))}</pre>'
            f'<div class="label">Output</div>{render_view(name, args, data, error)}'
            f'<details><summary>raw JSON result</summary><pre>{raw}</pre></details></section>')


async def run(demo_out: Path) -> str:
    settings = load_settings({
        "DMA_SMX_DIR": str(FIX / "smx"),
        "DMA_DBT_MANIFEST": str(FIX / "dbt" / "manifest.json"),
        "DMA_CONFIG_DIR": str(ROOT / "config"),
        "DMA_OUTPUT_DIR": str(demo_out),
    })
    async with Client(build_server(DmaTools(settings))) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        parts, nav = [], []
        for i, (name, args) in enumerate(CALLS):
            result = await client.call_tool(name, args)
            error = result.content[0].text if result.is_error else None
            t = tools[name]
            parts.append(section(i, name, t.description, t.annotations.read_only_hint, args, result.structured_content, error))
            nav.append(f'<a href="#t{i}">{esc(name)}{" (refused)" if error else ""}</a>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>DMA Tool Gallery</title><style>{CSS}</style></head><body><main><h1>Data Modeller Assistant: tool gallery</h1>'
            f'<p class="sub">Every MCP tool called over the MCP client on the synthetic fixtures. Regenerate with <code>uv run python scripts/tool_gallery.py</code>.</p>'
            f'<nav>{"".join(nav)}</nav>{"".join(parts)}</main></body></html>')


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        page = asyncio.run(run(Path(tmp) / "output"))
    target = ArtifactWriter(load_settings().output_dir).safe_path("tool_gallery.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
