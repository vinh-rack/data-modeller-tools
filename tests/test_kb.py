import pytest

from conftest import FIXTURES
from dma.kb import KbError, KnowledgeBase

KB = KnowledgeBase(FIXTURES / "kb")
REPORT = "22222222-2222-2222-2222-222222222222"


def test_list_serves_text_files_only():
    assert [f.name for f in KB.list()] == ["glossary.md", "report_map.yaml"]


@pytest.mark.parametrize("name", ["notes.bin", "../conftest.py", "..\conftest.py", "kb/glossary.md", "GLOSSARY.MD", ""])
def test_read_refuses_names_not_listed(name):
    with pytest.raises(KbError):
        KB.read(name)


def test_read_and_missing_folder():
    assert KB.read("glossary.md").content.startswith("# Glossary")
    assert KnowledgeBase(FIXTURES / "nope").list() == []


def test_report_map_entry_and_column_overrides():
    entry = KB.report(REPORT.upper())
    assert entry.model == "synthetic_fleet_eip"
    assert entry.gold_column("Booking", "booking_id") == "gold_fleet.f_booking.booking_id"
    assert entry.gold_column("Driver", "Driver Name") == "gold_fleet.d_driver.driver_nm"
    assert entry.gold_column("Unmapped", "x") is None


def test_report_map_unknown_and_malformed():
    with pytest.raises(KbError, match="not in"):
        KB.report("33333333-3333-3333-3333-333333333333")
    with pytest.raises(KbError, match="malformed"):
        KB.report("55555555-5555-5555-5555-555555555555")
