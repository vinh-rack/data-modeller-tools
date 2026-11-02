"""Read SMX workbooks into Table models. Read-only: workbooks are never saved."""

import re
from pathlib import Path
from typing import Any

import openpyxl

from dma.models import Column, Layer, ModelInfo, SourceRef, Table

_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
_QUALIFIED = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_SQL_WORDS = {"from", "join", "inner", "left", "right", "outer", "full", "cross", "select", "with", "and", "on", "as"}


class SmxLayoutError(ValueError):
    pass


def norm(v: Any) -> str:
    if v is None:
        return ""
    return " ".join(str(v).replace("\xa0", " ").split())


class SmxReader:
    def __init__(self, mapping: dict[str, Any]):
        self.mapping = mapping
        self._sheet_layers = {
            norm(name).lower(): Layer(layer)
            for layer, spec in mapping["layers"].items()
            for name in spec["sheets"]
        }
        self._aliases: list[tuple[str, str]] = [
            (norm(alias).lower(), field)
            for field, aliases in mapping["fields"].items()
            for alias in aliases
        ]
        self._true = {v.lower() for v in mapping.get("true_values", ["y"])}
        self._empty = {v.lower() for v in mapping.get("empty_values", ["", "-"])}
        self._pk_rule = re.compile(mapping["pk_rule_pattern"], re.I) if mapping.get("pk_rule_pattern") else None

    def layer_for_sheet(self, title: str) -> Layer | None:
        return self._sheet_layers.get(norm(title).lower())

    def read_workbook(self, path: Path, strict: bool = False) -> tuple[ModelInfo, list[Table]]:
        model = path.stem
        info = ModelInfo(name=model, path=str(path))
        tables: list[Table] = []
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                layer = self.layer_for_sheet(ws.title)
                if layer is None:
                    continue
                rows = [list(r) for r in ws.iter_rows(values_only=True)]
                try:
                    sheet_tables = self.read_sheet(model, ws.title, layer, rows)
                except SmxLayoutError as e:
                    if strict:
                        raise
                    info.warnings.append(str(e))
                    continue
                info.sheets[ws.title] = layer
                tables.extend(sheet_tables)
        finally:
            wb.close()
        info.table_count = len(tables)
        return info, tables

    def find_header(self, sheet: str, layer: Layer, rows: list[list[Any]]) -> tuple[int, dict[str, int]]:
        required = self.mapping["required_fields"].get(layer.value, self.mapping["required_fields"]["default"])
        scan = self.mapping.get("header_scan_rows", 15)
        seen: list[str] = []
        for idx, row in enumerate(rows[:scan]):
            cols = self._map_header(row)
            if self.mapping.get("schema_from_first_header") and "schema" not in cols and row:
                first = norm(row[0]).lower()
                if _IDENT.match(first) and not any(first == a for a, _ in self._aliases):
                    cols["schema"] = 0
            have = set(cols)
            if self.mapping.get("marker_rows_when_no_table_field") and "table" not in have and "column" in have:
                have.add("table")
            if all(f in have for f in required):
                return idx, cols
            if any(norm(c) for c in row):
                seen.append(" | ".join(norm(c) for c in row if norm(c)))
        raise SmxLayoutError(
            f"sheet '{sheet}' ({layer.value}): no header row with required fields {required} in first {scan} rows. "
            f"Rows seen: {seen[:3]}. Update config/smx_mapping.yaml."
        )

    def _map_header(self, row: list[Any]) -> dict[str, int]:
        cols: dict[str, int] = {}
        for ci, cell in enumerate(row):
            h = norm(cell).lower()
            if not h:
                continue
            for alias, field in self._aliases:
                if h == alias and field not in cols:
                    cols[field] = ci
                    break
        return cols

    def read_sheet(self, model: str, sheet: str, layer: Layer, rows: list[list[Any]]) -> list[Table]:
        header_idx, cols = self.find_header(sheet, layer, rows)
        marker_mode = "table" not in cols
        tables: dict[tuple[str, str], Table] = {}
        cur_schema = ""
        cur_table: Table | None = None

        def cell(row: list[Any], field: str) -> str:
            ci = cols.get(field)
            return norm(row[ci]) if ci is not None and ci < len(row) else ""

        def get_table(schema: str, name: str, excel_row: int) -> Table:
            key = (schema.lower(), name.lower())
            if key not in tables:
                tables[key] = Table(model=model, layer=layer, schema_name=schema.lower(), name=name, sheet=sheet, row=excel_row)
            return tables[key]

        for ri in range(header_idx + 1, len(rows)):
            row = rows[ri]
            excel_row = ri + 1
            if not any(norm(c) for c in row):
                continue
            col_val = cell(row, "column")

            if marker_mode:
                if _QUALIFIED.match(col_val.lower()):
                    schema, name = col_val.lower().split(".", 1)
                    cur_table = get_table(schema, name, excel_row)
                    self._add_table_level(cur_table, row, cell)
                    continue
                if cur_table is None:
                    continue
                table = cur_table
            else:
                schema_val = cell(row, "schema").lower()
                if schema_val and _IDENT.match(schema_val):
                    cur_schema = schema_val
                name = cell(row, "table")
                if name.lower() in self._empty:
                    continue
                table = get_table(cur_schema, name, excel_row)

            if col_val.lower() in self._empty or "column" not in cols:
                self._add_table_level(table, row, cell)
                continue

            rules = cell(row, "rules")
            pk_flag = cell(row, "is_pk").lower()
            is_pk = pk_flag in self._true if "is_pk" in cols else bool(self._pk_rule and self._pk_rule.search(rules))
            table.columns.append(Column(
                name=col_val,
                data_type=cell(row, "data_type") or None,
                is_pk=is_pk,
                is_fk=cell(row, "is_fk").lower() in self._true,
                source_table=cell(row, "source_table") or None,
                source_column=cell(row, "source_column") or None,
                rules=rules or None,
                row=excel_row,
            ))
            self._add_sources(table, cell(row, "source_table"), cell(row, "source_system"), cell(row, "source_schema"))

        return list(tables.values())

    def _add_table_level(self, table: Table, row: list[Any], cell) -> None:
        text = " ".join(t for t in (cell(row, "rules"), cell(row, "rules_comments"), cell(row, "description")) if t)
        if text:
            table.rules_text = f"{table.rules_text}\n{text}".strip()
        self._add_sources(table, cell(row, "source_table"), cell(row, "source_system"), cell(row, "source_schema"))

    def _add_sources(self, table: Table, source_cell: str, system: str, schema: str = "") -> None:
        for ref in parse_sources(source_cell, system or None, self._empty):
            if ref.schema_name is None and schema and _IDENT.match(schema.lower()):
                ref = SourceRef(schema_name=schema.lower(), table=ref.table, source_system=ref.source_system)
            if all(ref.qualified.lower() != s.qualified.lower() for s in table.sources):
                table.sources.append(ref)


def parse_sources(cell_text: str, source_system: str | None, empty: set[str] | None = None) -> list[SourceRef]:
    """Parse a source table cell. Each comma, semicolon or newline separated segment yields at most one table."""
    empty = empty or {"", "-"}
    refs: list[SourceRef] = []
    for segment in re.split(r"[,;\n]+", cell_text or ""):
        if segment.strip().lower() in empty:
            continue
        for token in _TOKEN.findall(segment):
            if token.lower() in _SQL_WORDS:
                continue
            parts = token.lower().split(".")
            if len(parts) >= 2:
                refs.append(SourceRef(schema_name=parts[-2], table=parts[-1], source_system=source_system))
            else:
                refs.append(SourceRef(table=parts[0], source_system=source_system))
            break
    return refs
