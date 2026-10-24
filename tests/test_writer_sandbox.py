import json
import os

import openpyxl
import pytest

from dma.writer import ArtifactWriter, WriterError


@pytest.fixture
def out(tmp_path):
    return tmp_path / "output"


@pytest.fixture
def writer(out):
    return ArtifactWriter(out, clock=lambda: "20260101T000000Z")


BAD_NAMES = [
    "../escape",
    "..",
    "a/../../escape",
    "output/../../escape",
    "sub/file",
    "sub\\file",
    "/etc/passwd",
    "C:\\Windows\\evil",
    "C:evil",
    "\\\\server\\share\\evil",
    "evil\x00name",
    "",
    ".hidden",
    "a..b",
]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_write_refuses_bad_names(writer, out, tmp_path, name):
    with pytest.raises(WriterError):
        writer.write("findings_md", name, "x")
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written == []


BAD_FILENAMES = [
    "../escape.md",
    "..\\escape.md",
    "a/b.md",
    "/abs.md",
    "C:\\abs.md",
    "C:rel.md",
    "\\\\server\\share\\x.md",
    "x\x00.md",
    "..",
    ".",
    "file.md:stream",
]


@pytest.mark.parametrize("filename", BAD_FILENAMES)
def test_safe_path_refuses_escapes(writer, filename):
    with pytest.raises(WriterError):
        writer.safe_path(filename)


def test_safe_path_accepts_plain_name(writer, out):
    assert writer.safe_path("ok.md") == (out / "ok.md").resolve()


def test_refuses_overwrite(writer, out):
    writer.write("findings_md", "review", "first")
    with pytest.raises(WriterError, match="overwrite"):
        writer.write("findings_md", "review", "second")
    assert (out / "20260101T000000Z_review.md").read_text(encoding="utf-8") == "first"


def test_refuses_symlink_target(writer, out, tmp_path):
    outside = tmp_path / "outside.md"
    out.mkdir(parents=True)
    try:
        os.symlink(outside, out / "20260101T000000Z_link.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")
    with pytest.raises(WriterError):
        writer.write("findings_md", "link", "x")
    assert not outside.exists()


def test_output_root_inside_baseline_is_refused(tmp_path):
    smx = tmp_path / "smx"
    smx.mkdir()
    with pytest.raises(WriterError, match="protected"):
        ArtifactWriter(smx / "output", protected=[smx])
    with pytest.raises(WriterError, match="protected"):
        ArtifactWriter(smx, protected=[smx])


def test_unknown_kind(writer):
    with pytest.raises(WriterError, match="kind"):
        writer.write("python_script", "x", "print(1)")


def test_writes_all_kinds(writer, out):
    md = writer.write("findings_md", "review", "# Findings\n")
    js = writer.write("decisions_json", "decisions", json.dumps({"FACT-001": "accepted"}))
    mm = writer.write("lineage_mermaid", "lineage", "flowchart LR\n  a --> b\n")
    xl = writer.write("proposed_smx_xlsx", "proposed", json.dumps({"GOLD - SDM": [{"schema name": "gold_fleet", "table name": "f_booking", "column name": "booking_id"}]}))
    for a in (md, js, mm, xl):
        assert os.path.dirname(a.path) == str(out.resolve())
    assert json.loads(open(js.path, encoding="utf-8").read()) == {"FACT-001": "accepted"}
    ws = openpyxl.load_workbook(xl.path, read_only=True)["GOLD - SDM"]
    assert [list(r) for r in ws.iter_rows(values_only=True)] == [["schema name", "table name", "column name"], ["gold_fleet", "f_booking", "booking_id"]]


def test_invalid_json_rejected(writer, out):
    with pytest.raises(WriterError, match="JSON"):
        writer.write("decisions_json", "bad", "{not json")
    assert not any(out.glob("*.json"))
