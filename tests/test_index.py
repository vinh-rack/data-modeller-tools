import hashlib

import openpyxl
import pytest

from conftest import FIXTURES, SMX_DIR
from dma.index.model_index import ModelIndex
from dma.index.smx_reader import SmxLayoutError, SmxReader, parse_sources
from dma.models import Layer


def _qualified(tables):
    return {t.qualified for t in tables}


def test_reads_both_layout_families(index):
    assert {m.name for m in index.list_models()} == {"synthetic_fleet_eip", "synthetic_logistics_canonical"}
    for m in index.list_models():
        assert set(m.sheets.values()) == set(Layer)
        assert m.warnings == []


def test_non_layer_sheets_are_ignored(index):
    eip = index.models["synthetic_fleet_eip"]
    assert "Review Comments" not in eip.sheets
    assert "Overview" not in index.models["synthetic_logistics_canonical"].sheets


def test_schema_from_first_header_cell(index):
    t = index.resolve("gold_logistics.d_vehicle")[0]
    assert t.layer is Layer.GOLD
    assert t.grain == ["vehicle_id"]
    assert t.bu == "logistics"


def test_pk_from_rules_text_when_no_pk_column(index):
    assert index.resolve("slvr_stg.booking")[0].grain == ["booking_num"]


def test_source_system_used_as_schema(index):
    t = index.resolve("gold_logistics.d_vehicle")[0]
    assert [s.qualified for s in t.sources] == ["slvr_cdm.vehicle_master"]


def test_bu_from_schema(index):
    assert index.resolve("gold_fleet.f_booking")[0].bu == "fleet"
    assert index.resolve("slvr_cdm.booking")[0].bu == "cdm"
    assert index.resolve("slvr_stg.booking")[0].bu is None


def test_find_by_bronze_is_transitive(index):
    found = _qualified(index.by_bronze("brnz_fleetops.booking"))
    assert {"slvr_stg.booking", "slvr_cdm.booking", "gold_fleet.f_booking", "gold_fleet.agg_booking_by_driver"} <= found
    assert "gold_fleet.d_driver" not in found


def test_find_by_grain(index):
    assert _qualified(index.by_grain(["vehicle_id"])) == {"slvr_cdm.vehicle", "gold_fleet.d_vehicle", "slvr_cdm.vehicle_master", "gold_logistics.d_vehicle"}


def test_bronze_sources_resolve_unqualified_bronze(index):
    assert index.bronze_sources(index.resolve("gold_logistics.d_vehicle")[0]) == ["brnz_depot.vehicle_master"]


def test_parse_sources_skips_sql_words_and_aliases():
    refs = parse_sources("from slvr_stg.prospect T1,\ninventdim, -", "bronze")
    assert [r.qualified for r in refs] == ["slvr_stg.prospect", "inventdim"]


def test_marker_row_layout(tmp_path, mapping, rules_cfg):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Silver - Staging"
    ws.append(["target schema", "target column name", "data type", "set no.", "source table name", "source column name", "join/rule/filter applied"])
    ws.append(["slvr_stg", "slvr_stg.asset", None, 1, "brnz_x.asset", None, "1:1 mapping"])
    ws.append(["slvr_stg", "asset_num", "string", 1, "brnz_x.asset", "assetno", "pk"])
    wb.save(tmp_path / "marker.xlsx")
    idx = ModelIndex.build(tmp_path, mapping, rules_cfg, strict=True)
    t = idx.resolve("slvr_stg.asset")[0]
    assert t.grain == ["asset_num"]
    assert [s.qualified for s in t.sources] == ["brnz_x.asset"]


def test_unknown_layout_fails_loudly(tmp_path, mapping):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "GOLD - SDM"
    ws.append(["something", "else"])
    wb.save(tmp_path / "bad.xlsx")
    reader = SmxReader(mapping)
    with pytest.raises(SmxLayoutError, match="GOLD - SDM"):
        reader.read_workbook(tmp_path / "bad.xlsx", strict=True)
    info, _ = reader.read_workbook(tmp_path / "bad.xlsx")
    assert info.warnings and "smx_mapping.yaml" in info.warnings[0]


def test_reading_never_modifies_workbooks(mapping, rules_cfg):
    before = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in SMX_DIR.glob("*.xlsx")}
    ModelIndex.build(SMX_DIR, mapping, rules_cfg, strict=True)
    after = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in SMX_DIR.glob("*.xlsx")}
    assert before == after


def test_reads_the_cdm_v1_layout(mapping, rules_cfg):
    idx = ModelIndex.build(FIXTURES / "smx_cdm_v1", mapping, rules_cfg, strict=True)
    info = idx.models["synthetic_cdm_v1"]
    assert sorted(info.sheets) == ["Bronze - Landing", "Gold - SDM", "Silver - IDM", "Silver- Staging"]
    by = {t.qualified: t for t in idx.tables}
    assert sorted((t.layer.value, t.qualified) for t in idx.tables) == [("bronze", "address"), ("gold", "gold_party.d_address"), ("silver_idm", "slvr_cdm.address"), ("silver_staging", "slvr_stg.address")]
    assert [c.name for c in by["slvr_stg.address"].columns if c.is_pk] == ["address_num"]
    assert [s.qualified for s in by["slvr_cdm.address"].sources] == ["slvr_stg.address"]
    assert [s.qualified for s in by["gold_party.d_address"].sources] == ["slvr_cdm.address"]
    assert idx.bronze_sources(by["gold_party.d_address"]) == ["address"]
