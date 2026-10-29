"""Table-level lineage from the SMX index as nodes, edges and a Mermaid flowchart (FR-L3).

A node is confirmed when its table is in the index, and inferred when it is only named as a source in an SMX cell.
"""

from collections import deque
from typing import Literal

from pydantic import BaseModel

from dma.index.model_index import ModelIndex
from dma.kb import ReportEntry
from dma.lineage.sql_lineage import LineageError
from dma.models import Layer, SourceRef, Table

Direction = Literal["upstream", "downstream", "both"]
MAX_DEPTH = 20
_LAYER_TITLES = {Layer.BRONZE: "Bronze", Layer.SILVER_STAGING: "Silver Staging", Layer.SILVER_IDM: "Silver IDM", Layer.GOLD: "Gold"}


class LineageNode(BaseModel):
    table: str
    layer: Layer | None
    status: Literal["confirmed", "inferred", "unknown"]


class LineageEdge(BaseModel):
    source: str
    target: str


class LineageGraph(BaseModel):
    root: list[str]
    direction: Direction
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    mermaid: str


class _Builder:
    def __init__(self, index: ModelIndex):
        self.index = index
        self.nodes: dict[str, LineageNode] = {}
        self.edges: set[tuple[str, str]] = set()

    def table(self, t: Table) -> str:
        self.nodes.setdefault(t.qualified, LineageNode(table=t.qualified, layer=t.layer, status="confirmed"))
        return t.qualified

    def walk(self, start: list[Table], upstream: bool) -> None:
        queue = deque((t, 0) for t in start)
        seen = {id(t) for t in start}
        while queue:
            t, depth = queue.popleft()
            self.table(t)
            if depth >= MAX_DEPTH:
                continue
            if upstream:
                parents, unresolved = parent_tables(self.index, t)
                for src in unresolved:
                    self.nodes.setdefault(src.qualified, LineageNode(table=src.qualified, layer=self.index.classifier.layer_of_source(src), status="inferred"))
                    self.edges.add((src.qualified, t.qualified))
            else:
                parents = [c for c in self.index.downstream(t) if any(p is t for p in parent_tables(self.index, c)[0])]
            for nxt in parents:
                self.edges.add((self.table(nxt), t.qualified) if upstream else (t.qualified, self.table(nxt)))
                if id(nxt) not in seen:
                    seen.add(id(nxt))
                    queue.append((nxt, depth + 1))


def parent_tables(index: ModelIndex, t: Table) -> tuple[list[Table], list[SourceRef]]:
    """Indexed parent tables of a table, plus the sources that match no indexed table."""
    parents: list[Table] = []
    unresolved: list[SourceRef] = []
    if t.layer is Layer.BRONZE:
        # Bronze sources are source-system tables outside the lakehouse, so Bronze is where lineage starts.
        return parents, unresolved
    for src in t.sources:
        layer = index.classifier.layer_of_source(src)
        # An unqualified SMX source like "vehicle_master" matches that name in every layer and model.
        # Keep the source's own layer when known, else upstream layers only, and prefer the table's own model.
        found = [p for p in index.resolve(src.qualified) if p is not t and (p.layer is layer if layer else p.layer.order <= t.layer.order)]
        same_model = [p for p in found if p.model == t.model]
        if same_model or found:
            parents.extend(same_model or found)
        else:
            unresolved.append(src)
    return parents, unresolved


def table_lineage(index: ModelIndex, table: str, direction: Direction = "upstream") -> LineageGraph:
    start = index.resolve(table)
    if not start:
        raise LineageError(f"table {table!r} is not in any SMX model. Use schema.table or a bare name from get_model.")
    b = _Builder(index)
    if direction in ("upstream", "both"):
        b.walk(start, upstream=True)
    if direction in ("downstream", "both"):
        b.walk(start, upstream=False)
    roots = sorted({t.qualified for t in start})
    return graph(b.nodes, b.edges, roots, direction)


def graph(nodes: dict[str, LineageNode], edges: set[tuple[str, str]], roots: list[str], direction: Direction, other_title: str = "Other") -> LineageGraph:
    order = {layer: i for i, layer in enumerate(_LAYER_TITLES)}
    ordered = sorted(nodes.values(), key=lambda n: (order.get(n.layer, len(order)), n.table))
    ids = {n.table: f"n{i}" for i, n in enumerate(ordered)}
    lines = ["flowchart LR"]
    for layer, title in [*_LAYER_TITLES.items(), (None, other_title)]:
        members = [n for n in ordered if n.layer == layer]
        if members:
            lines.append(f"  subgraph {layer.value if layer else 'other'} [{title}]")
            lines += [f'    {ids[n.table]}["{_label(n.table)}"]' for n in members]
            lines.append("  end")
    lines += [f"  {ids[s]} --> {ids[t]}" for s, t in sorted(edges, key=lambda e: (ids[e[0]], ids[e[1]]))]
    lines.append("  classDef root stroke-width:3px")
    lines.append("  classDef inferred stroke-dasharray: 4 4")
    lines.append("  classDef unknown stroke-dasharray: 2 2,fill:#eee")
    for status in ("inferred", "unknown"):
        marked = [ids[n.table] for n in ordered if n.status == status]
        if marked:
            lines.append(f"  class {','.join(marked)} {status}")
    if roots:
        lines.append(f"  class {','.join(ids[r] for r in roots if r in ids)} root")
    return LineageGraph(root=roots, direction=direction, nodes=ordered, edges=[LineageEdge(source=s, target=t) for s, t in sorted(edges)], mermaid="\n".join(lines) + "\n")


class ReportTableLineage(BaseModel):
    pbi_table: str
    gold_table: str | None
    bronze: list[str]
    status: Literal["confirmed", "unknown"]
    note: str | None = None


class ReportLineage(BaseModel):
    report_id: str
    report_name: str | None
    tables: list[ReportTableLineage]
    mermaid: str


def report_lineage(index: ModelIndex, entry: ReportEntry, pbi_tables: list[str]) -> ReportLineage:
    """Power BI table -> Gold -> Bronze (FR-L4). A table without a mapped, indexed Gold table is marked unknown, never guessed."""
    b = _Builder(index)
    rows = []
    for pbi in sorted(set(pbi_tables) | set(entry.tables)):
        node = f"report.{pbi}"
        mapped = entry.tables.get(pbi)
        gold = [t for t in index.resolve(mapped.table) if t.layer is Layer.GOLD] if mapped else []
        if pbi not in pbi_tables:
            note = "in the report map but not in the Power BI model"
        elif not mapped:
            note = "not in the report map"
        elif not gold:
            note = f"mapped to {mapped.table}, which is not a Gold table in any SMX"
        else:
            note = None
        b.nodes[node] = LineageNode(table=node, layer=None, status="unknown" if note else "confirmed")
        if note:
            rows.append(ReportTableLineage(pbi_table=pbi, gold_table=mapped.table if mapped else None, bronze=[], status="unknown", note=note))
            continue
        b.walk(gold, upstream=True)
        for g in gold:
            b.edges.add((g.qualified, node))
        bronze = sorted({src for t in gold for src in index.bronze_sources(t)})
        rows.append(ReportTableLineage(pbi_table=pbi, gold_table=gold[0].qualified, bronze=bronze, status="confirmed" if bronze else "unknown", note=None if bronze else "no Bronze source found upstream"))
    g = graph(b.nodes, b.edges, [], "upstream", other_title=f"Power BI: {entry.name or entry.report_id}")
    return ReportLineage(report_id=entry.report_id, report_name=entry.name, tables=rows, mermaid=g.mermaid)


def _label(text: str) -> str:
    return text.replace('"', "#quot;")
