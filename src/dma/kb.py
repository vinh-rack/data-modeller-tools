"""Knowledge base folder (FR-K2): glossary, conventions and the report-to-model map, read-only.

Only plain files directly inside DMA_KB_DIR are served, by exact name from the listing, so a name can never point outside the folder.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

REPORT_MAP = "report_map.yaml"
KB_SUFFIXES = {".md", ".txt", ".yaml", ".yml"}
MAX_KB_BYTES = 1_000_000


class KbError(ValueError):
    pass


class KbFile(BaseModel):
    name: str
    bytes: int


class KbDocument(BaseModel):
    name: str
    content: str


class TableMap(BaseModel):
    table: str = Field(description="Gold table as schema.table")
    columns: dict[str, str] = Field(default_factory=dict, description="Power BI column name to Gold column name, where they differ")


class ReportEntry(BaseModel):
    report_id: str
    name: str | None = None
    model: str | None = Field(default=None, description="SMX model that holds the report's Gold tables")
    tables: dict[str, TableMap] = Field(default_factory=dict, description="Power BI table name to its Gold table")

    def gold_column(self, pbi_table: str, pbi_column: str) -> str | None:
        """schema.table.column in Gold for a Power BI column, or None when its table is not mapped."""
        m = self.tables.get(pbi_table)
        return f"{m.table}.{m.columns.get(pbi_column, pbi_column)}" if m else None


class KnowledgeBase:
    def __init__(self, kb_dir: Path):
        self.kb_dir = Path(kb_dir)

    def list(self) -> list[KbFile]:
        if not self.kb_dir.is_dir():
            return []
        return [KbFile(name=p.name, bytes=p.stat().st_size) for p in sorted(self.kb_dir.iterdir()) if p.is_file() and not p.is_symlink() and p.suffix.lower() in KB_SUFFIXES]

    def read(self, name: str) -> KbDocument:
        known = {f.name: f for f in self.list()}
        if name not in known:
            raise KbError(f"no knowledge base file named {name!r}. Known: {sorted(known)}")
        if known[name].bytes > MAX_KB_BYTES:
            raise KbError(f"{name} is {known[name].bytes} bytes, over the {MAX_KB_BYTES} byte limit")
        return KbDocument(name=name, content=(self.kb_dir / name).read_text(encoding="utf-8"))

    def report(self, report_id: str) -> ReportEntry:
        raw = yaml.safe_load(self.read(REPORT_MAP).content) if REPORT_MAP in {f.name for f in self.list()} else None
        reports = (raw or {}).get("reports") or {}
        key = next((k for k in reports if str(k).lower() == report_id.strip().lower()), None)
        if key is None:
            raise KbError(f"report {report_id} is not in {self.kb_dir / REPORT_MAP}. Add it there, or ask the modeller which Gold tables it uses.")
        entry = dict(reports[key] or {})
        # A table may be given as a plain "schema.table" string when its column names match Gold.
        entry["tables"] = {t: ({"table": v} if isinstance(v, str) else v) for t, v in (entry.get("tables") or {}).items()}
        try:
            return ReportEntry(report_id=str(key), **entry)
        except ValidationError as e:
            raise KbError(f"report {report_id} in {REPORT_MAP} is malformed: {e}") from None
