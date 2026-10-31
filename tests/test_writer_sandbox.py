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


SID = "0123456789abcdef0123456789abcdef"
BAD_SESSION_IDS = ["", "..", "../" + SID[:29], SID.upper(), SID[:31], SID + "0", SID[:31] + "/", SID[:31] + "\\", "C:" + SID[:30], None]


@pytest.mark.parametrize("session_id", BAD_SESSION_IDS)
def test_session_log_refuses_bad_ids(writer, tmp_path, session_id):
    with pytest.raises(WriterError, match="session_id"):
        writer.append_session_event(session_id, {"type": "x"}, create=True)
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_session_log_is_append_only(writer, out):
    writer.append_session_event(SID, {"n": 1}, create=True)
    writer.append_session_event(SID, {"n": 2})
    assert writer.read_session_events(SID) == [{"n": 1}, {"n": 2}]
    assert (out / ".sessions" / SID / "events.jsonl").is_file()
    with pytest.raises(FileExistsError):
        writer.append_session_event(SID, {"n": 3}, create=True)
    assert writer.read_session_events(SID) == [{"n": 1}, {"n": 2}]


def test_session_append_needs_existing_session(writer):
    with pytest.raises(WriterError, match="unknown session"):
        writer.append_session_event(SID, {"n": 1})
    with pytest.raises(WriterError, match="unknown session"):
        writer.read_session_events(SID)


def test_session_event_size_cap(writer, monkeypatch):
    import dma.writer as w

    writer.append_session_event(SID, {"n": 1}, create=True)
    with pytest.raises(WriterError, match="larger than"):
        writer.append_session_event(SID, {"blob": "x" * w.MAX_EVENT_BYTES})
    monkeypatch.setattr(w, "MAX_SESSION_BYTES", 15)
    with pytest.raises(WriterError, match="exceed"):
        writer.append_session_event(SID, {"n": 2})
    assert writer.read_session_events(SID) == [{"n": 1}]


def test_session_dir_symlink_refused(writer, out, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    out.mkdir(parents=True)
    try:
        os.symlink(outside, out / ".sessions", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")
    with pytest.raises(WriterError, match="symlink"):
        writer.append_session_event(SID, {"n": 1}, create=True)
    assert list(outside.iterdir()) == []


def test_session_dir_junction_refused(writer, out, tmp_path):
    if os.name != "nt":
        pytest.skip("junctions are Windows only")
    import subprocess

    outside = tmp_path / "outside"
    outside.mkdir()
    (out / ".sessions").mkdir(parents=True)
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(out / ".sessions" / SID), str(outside)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("could not create a junction")
    with pytest.raises(WriterError):
        writer.append_session_event(SID, {"n": 1})
    assert list(outside.iterdir()) == []


def test_list_session_ids(writer, out):
    assert writer.list_session_ids() == []
    writer.append_session_event(SID, {"n": 1}, create=True)
    (out / ".sessions" / "not-a-session").mkdir()
    assert writer.list_session_ids() == [SID]


def test_artifacts_never_land_in_sessions(writer):
    with pytest.raises(WriterError):
        writer.safe_path(".sessions/x.md")


def test_dbt_pull_log_is_append_only(writer, out):
    assert writer.read_dbt_pulls() == []
    writer.append_dbt_pull({"n": 1})
    writer.append_dbt_pull({"n": 2})
    assert writer.read_dbt_pulls() == [{"n": 1}, {"n": 2}]
    assert writer.dbt_pull_log() == out / ".dbt" / "pulls.jsonl"


def test_dbt_pull_log_size_cap(writer, monkeypatch):
    import dma.writer as w

    writer.append_dbt_pull({"n": 1})
    monkeypatch.setattr(w, "MAX_PULL_LOG_BYTES", 15)
    with pytest.raises(WriterError, match="exceed"):
        writer.append_dbt_pull({"n": 2})
    assert writer.read_dbt_pulls() == [{"n": 1}]


def test_dbt_dir_symlink_refused(writer, out, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    out.mkdir(parents=True)
    try:
        os.symlink(outside, out / ".dbt", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")
    with pytest.raises(WriterError, match="symlink"):
        writer.append_dbt_pull({"n": 1})
    assert list(outside.iterdir()) == []


def test_dbt_dir_junction_refused(writer, out, tmp_path):
    if os.name != "nt":
        pytest.skip("junctions are Windows only")
    import subprocess

    outside = tmp_path / "outside"
    outside.mkdir()
    out.mkdir(parents=True)
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(out / ".dbt"), str(outside)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("could not create a junction")
    with pytest.raises(WriterError):
        writer.append_dbt_pull({"n": 1})
    assert list(outside.iterdir()) == []


def test_artifacts_never_land_in_dbt_dir(writer):
    with pytest.raises(WriterError):
        writer.safe_path(".dbt/pulls.jsonl")
