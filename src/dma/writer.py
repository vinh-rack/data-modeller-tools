"""The only module allowed to write files. Writes new files inside the output root and nowhere else."""

import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

import openpyxl
from pydantic import BaseModel

ArtifactKind = Literal["findings_md", "decisions_json", "proposed_smx_xlsx", "lineage_mermaid"]
EXTENSIONS: dict[str, str] = {
    "findings_md": ".md",
    "decisions_json": ".json",
    "proposed_smx_xlsx": ".xlsx",
    "lineage_mermaid": ".mmd",
}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
MAX_BYTES = 5 * 1024 * 1024


class WriterError(ValueError):
    pass


class WrittenArtifact(BaseModel):
    kind: str
    path: str
    bytes: int


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

    def _render(self, kind: str, content: str) -> bytes:
        if kind == "decisions_json":
            try:
                return (json.dumps(json.loads(content), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            except json.JSONDecodeError as e:
                raise WriterError(f"decisions_json content is not valid JSON: {e}") from e
        if kind == "proposed_smx_xlsx":
            return _rows_to_xlsx(content)
        return content.encode("utf-8")


def _rows_to_xlsx(content: str) -> bytes:
    """Content is JSON: {"<sheet name>": [{"<header>": value, ...}, ...]}. Headers come from the rows, in first-seen order."""
    try:
        sheets = json.loads(content)
    except json.JSONDecodeError as e:
        raise WriterError(f"proposed_smx_xlsx content is not valid JSON: {e}") from e
    if not isinstance(sheets, dict) or not all(isinstance(v, list) for v in sheets.values()):
        raise WriterError('proposed_smx_xlsx content must be {"sheet": [row objects]}')
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(str(title)[:31])
        headers: list[str] = []
        for row in rows:
            for k in row:
                if k not in headers:
                    headers.append(k)
        ws.append(headers)
        for row in rows:
            ws.append([row.get(h) for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
