"""Table-level lineage from the SMX index as nodes, edges and a Mermaid flowchart (FR-L3).

A node is confirmed when its table is in the index, and inferred when it is only named as a source in an SMX cell.
"""

from collections import deque
from typing import Literal

from pydantic import BaseModel

from dma.index.model_index import ModelIndex
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


def graph(nodes: dict[str, LineageNode], edges: set[tuple[str, str]], roots: list[str], direction: Direction) -> LineageGraph:
    order = {layer: i for i, layer in enumerate(_LAYER_TITLES)}
    ordered = sorted(nodes.values(), key=lambda n: (order.get(n.layer, len(order)), n.table))
    ids = {n.table: f"n{i}" for i, n in enumerate(ordered)}
    lines = ["flowchart LR"]
    for layer, title in [*_LAYER_TITLES.items(), (None, "Other")]:
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
    lines.append(f"  class {','.join(ids[r] for r in roots if r in ids)} root")
    return LineageGraph(root=roots, direction=direction, nodes=ordered, edges=[LineageEdge(source=s, target=t) for s, t in sorted(edges)], mermaid="\n".join(lines) + "\n")


def _label(text: str) -> str:
    return text.replace('"', "#quot;")
