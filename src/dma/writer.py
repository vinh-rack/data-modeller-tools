"""The only module allowed to write files. Writes new artifacts and append-only session logs inside the output root and nowhere else."""

import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from pydantic import BaseModel

ArtifactKind = Literal["findings_md", "decisions_json", "proposed_smx_xlsx", "lineage_mermaid", "requirements_md"]
EXTENSIONS: dict[str, str] = {
    "findings_md": ".md",
    "decisions_json": ".json",
    "proposed_smx_xlsx": ".xlsx",
    "lineage_mermaid": ".mmd",
    "requirements_md": ".md",
}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
MAX_BYTES = 5 * 1024 * 1024
SESSIONS_DIR = ".sessions"
EVENTS_FILE = "events.jsonl"
SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_SESSION_BYTES = 50 * 1024 * 1024
DBT_DIR = ".dbt"
PULL_LOG = "pulls.jsonl"
MAX_PULL_LOG_BYTES = 5 * 1024 * 1024


class WriterError(ValueError):
    pass


class WrittenArtifact(BaseModel):
    kind: str
    path: str
    bytes: int
    notes: list[str] = []


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


class ArtifactWriter:
    def __init__(self, root: Path, protected: list[Path] | None = None, clock: Callable[[], str] = _utc_stamp):
        self.root = Path(root).resolve()
        self.clock = clock
        for p in protected or []:
            p = Path(p).resolve()
            if self.root == p or self.root.is_relative_to(p):
                raise WriterError(f"output root {self.root} must not be inside protected folder {p}")

    def safe_path(self, filename: str) -> Path:
        """Resolve filename inside the root. Raises WriterError on any escape attempt."""
        if not filename or "\x00" in filename:
            raise WriterError("empty filename or null byte")
        if any(ch in filename for ch in "/\\:"):
            raise WriterError(f"path separators and drive or stream markers are not allowed: {filename!r}")
        candidate = Path(filename)
        if candidate.is_absolute() or candidate.drive or candidate.anchor:
            raise WriterError(f"absolute paths are not allowed: {filename!r}")
        if len(candidate.parts) != 1 or candidate.name in (".", ".."):
            raise WriterError(f"subfolders and traversal are not allowed: {filename!r}")
        target = (self.root / candidate).resolve()
        if target.parent != self.root or not target.is_relative_to(self.root):
            raise WriterError(f"path escapes the output root: {filename!r}")
        if (self.root / candidate).is_symlink():
            raise WriterError(f"refusing to write through a symlink: {filename!r}")
        return target

    def write(self, kind: str, name: str, content: str) -> WrittenArtifact:
        if kind not in EXTENSIONS:
            raise WriterError(f"kind must be one of {sorted(EXTENSIONS)}, got {kind!r}")
        if not NAME_RE.match(name or "") or ".." in name:
            raise WriterError(f"name must match {NAME_RE.pattern} with no '..', got {name!r}")
        if len(content.encode("utf-8")) > MAX_BYTES:
            raise WriterError(f"content larger than {MAX_BYTES} bytes")

        self.root.mkdir(parents=True, exist_ok=True)
        target = self.safe_path(f"{self.clock()}_{name}{EXTENSIONS[kind]}")
        data = self._render(kind, content)
        try:
            with open(target, "xb") as fh:
                fh.write(data)
        except FileExistsError as e:
            raise WriterError(f"refusing to overwrite existing file {target.name}") from e
        return WrittenArtifact(kind=kind, path=str(target), bytes=len(data))

    def session_log(self, session_id: str) -> Path:
        """Path of output/.sessions/<id>/events.jsonl. Raises WriterError on a bad id, a symlink or a junction anywhere on the way."""
        if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
            raise WriterError(f"session_id must be 32 lowercase hex characters, got {session_id!r}")
        sessions = self.root / SESSIONS_DIR
        folder = sessions / session_id
        target = folder / EVENTS_FILE
        for p in (sessions, folder, target):
            if p.is_symlink():
                raise WriterError(f"refusing to use a symlink in the session path: {p.name}")
        # resolve() follows junctions too, so a redirected folder no longer matches the expected path.
        if target.resolve() != target:
            raise WriterError(f"session path escapes the output root: {session_id}")
        return target

    def append_session_event(self, session_id: str, record: dict, create: bool = False) -> None:
        """Append one JSON line to a session log. create=True starts a new session and fails if it exists. Lines are never rewritten."""
        target = self.session_log(session_id)
        line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if len(line) > MAX_EVENT_BYTES:
            raise WriterError(f"session event larger than {MAX_EVENT_BYTES} bytes")
        if create:
            target.parent.mkdir(parents=True, exist_ok=False)
            self.session_log(session_id)
            mode = "xb"
        else:
            if not target.is_file():
                raise WriterError(f"unknown session: {session_id}")
            if target.stat().st_size + len(line) > MAX_SESSION_BYTES:
                raise WriterError(f"session log would exceed {MAX_SESSION_BYTES} bytes")
            mode = "ab"
        with open(target, mode) as fh:
            fh.write(line)

    def read_session_events(self, session_id: str) -> list[dict]:
        target = self.session_log(session_id)
        if not target.is_file():
            raise WriterError(f"unknown session: {session_id}")
        with open(target, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def dbt_pull_log(self) -> Path:
        """Path of output/.dbt/pulls.jsonl, the append-only record of daily dbt repo pulls. Same symlink and junction checks as session logs."""
        folder = self.root / DBT_DIR
        target = folder / PULL_LOG
        for p in (folder, target):
            if p.is_symlink():
                raise WriterError(f"refusing to use a symlink in the dbt pull log path: {p.name}")
        if target.resolve() != target:
            raise WriterError("dbt pull log path escapes the output root")
        return target

    def append_dbt_pull(self, record: dict) -> None:
        """Append one JSON line to the dbt pull log. Lines are never rewritten; the last line is the latest pull."""
        target = self.dbt_pull_log()
        line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if len(line) > MAX_EVENT_BYTES:
            raise WriterError(f"dbt pull record larger than {MAX_EVENT_BYTES} bytes")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.dbt_pull_log()
        if target.is_file() and target.stat().st_size + len(line) > MAX_PULL_LOG_BYTES:
            raise WriterError(f"dbt pull log would exceed {MAX_PULL_LOG_BYTES} bytes")
        with open(target, "ab") as fh:
            fh.write(line)

    def read_dbt_pulls(self) -> list[dict]:
        target = self.dbt_pull_log()
        if not target.is_file():
            return []
        with open(target, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def list_session_ids(self) -> list[str]:
        sessions = self.root / SESSIONS_DIR
        if not sessions.is_dir() or sessions.is_symlink():
            return []
        return sorted(p.name for p in sessions.iterdir() if SESSION_ID_RE.match(p.name) and not p.is_symlink() and (p / EVENTS_FILE).is_file())

    def _render(self, kind: str, content: str) -> bytes:
        if kind == "decisions_json":
            try:
                return (json.dumps(json.loads(content), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            except json.JSONDecodeError as e:
                raise WriterError(f"decisions_json content is not valid JSON: {e}") from e
        if kind == "proposed_smx_xlsx":
            return _rows_to_xlsx(content)
        return content.encode("utf-8")


XLSX_STYLE = {"font": "Calibri", "font_size": 9, "header_fill": "FFC000", "reviewer_fill": "EDEDED", "border_color": "BFBFBF", "min_width": 8, "max_width": 60}
_HEX = re.compile(r"^[0-9A-Fa-f]{6}$")


def _rows_to_xlsx(content: str) -> bytes:
    """Content is JSON {"sheets": [{"name", "headers", "rows", "tab_color"?, "reviewer_columns"?}], "style"?: {...}}.
    Exactly these sheets are written, in order, each with its header row in the given order. A row key that is not one of
    the sheet's headers is refused. The style follows the shared SMX files, plus a frozen header and sized columns."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise WriterError(f"proposed_smx_xlsx content is not valid JSON: {e}") from e
    sheets = data.get("sheets") if isinstance(data, dict) else None
    if not isinstance(sheets, list) or not sheets or not all(isinstance(s, dict) and isinstance(s.get("headers"), list) and isinstance(s.get("rows"), list) for s in sheets):
        raise WriterError('proposed_smx_xlsx content must be {"sheets": [{"name": ..., "headers": [...], "rows": [...]}]}')
    style = {**XLSX_STYLE, **(data.get("style") or {})}
    for key in ("header_fill", "reviewer_fill", "border_color"):
        if not _HEX.match(str(style[key])):
            raise WriterError(f"style {key} must be a 6-digit hex colour, got {style[key]!r}")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet in sheets:
        headers = [str(h) for h in sheet["headers"]]
        ws = wb.create_sheet(str(sheet.get("name", ""))[:31])
        ws.append(headers)
        for row in sheet["rows"]:
            extra = set(row) - set(headers)
            if extra:
                raise WriterError(f"sheet {sheet.get('name')!r}: row keys {sorted(extra)} are not headers {headers}")
            ws.append([row.get(h) for h in headers])
        _style_sheet(ws, headers, len(sheet["rows"]), style, sheet.get("tab_color"), set(sheet.get("reviewer_columns") or []))
    wb.properties.creator = "Data Modeller Assistant"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _style_sheet(ws, headers: list[str], n_rows: int, style: dict, tab_color: str | None, reviewer: set[str]) -> None:
    side = Side(style="thin", color=style["border_color"])
    border = Border(left=side, right=side, top=side, bottom=side)
    body_font = Font(name=style["font"], size=style["font_size"])
    head_font = Font(name=style["font"], size=style["font_size"], bold=True)
    head_fill = PatternFill("solid", fgColor=style["header_fill"])
    review_fill = PatternFill("solid", fgColor=style["reviewer_fill"])
    last = get_column_letter(len(headers))
    for ci, h in enumerate(headers, 1):
        cell = ws.cell(1, ci)
        cell.font, cell.fill, cell.border = head_font, head_fill, border
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        if h in reviewer:
            cell.comment = Comment("Filled by the reviewers. The assistant leaves this empty.", "Data Modeller Assistant")
        longest = max([len(h)] + [len(str(ws.cell(r, ci).value)) for r in range(2, n_rows + 2) if ws.cell(r, ci).value is not None])
        ws.column_dimensions[get_column_letter(ci)].width = min(max(longest + 2, style["min_width"]), style["max_width"])
        for r in range(2, n_rows + 2):
            c = ws.cell(r, ci)
            c.font, c.border = body_font, border
            c.alignment = Alignment(vertical="top", wrap_text=len(str(c.value or "")) > style["max_width"])
            if h in reviewer:
                c.fill = review_fill
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{last}{n_rows + 1}"
    if tab_color and _HEX.match(str(tab_color)):
        ws.sheet_properties.tabColor = str(tab_color)
