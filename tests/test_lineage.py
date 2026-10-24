import pytest

from conftest import FIXTURES
from dma.lineage.dbt_manifest import DbtManifest, ManifestError
from dma.lineage.sql_lineage import LineageError, column_lineage

MANIFEST = FIXTURES / "dbt" / "manifest.json"
SQL = (FIXTURES / "sql" / "fct_booking.sql").read_text(encoding="utf-8")


def test_dbt_upstream_walks_to_sources():
    up = DbtManifest.load(MANIFEST).upstream("fct_booking")
    assert [b.table for b in up.bronze_tables] == ["brnz_fleetops.booking", "brnz_fleetops.meter_change"]
    assert up.via_models == ["model.synthetic_fleet.stg_booking", "model.synthetic_fleet.stg_meter_change"]
    assert up.missing_nodes == ["model.synthetic_fleet.removed_model"]
    assert up.manifest_schema.endswith("v12.json")


def test_dbt_find_by_alias_and_unique_id():
    m = DbtManifest.load(MANIFEST)
    assert m.find_model("f_booking") == "model.synthetic_fleet.fct_booking"
    assert m.find_model("model.synthetic_fleet.stg_booking") == "model.synthetic_fleet.stg_booking"


def test_dbt_unknown_model():
    with pytest.raises(ManifestError, match="no dbt model"):
        DbtManifest.load(MANIFEST).upstream("nope")


@pytest.mark.parametrize("dialect", ["redshift", "databricks"])
def test_sql_lineage_through_cte_and_join(dialect):
    res = column_lineage(SQL, "total_amount", dialect)
    assert [(s.table, s.column) for s in res.sources] == [("brnz_fleetops.booking", "amount"), ("brnz_fleetops.meter_change", "reading")]


def test_sql_lineage_rejects_unknown_dialect():
    with pytest.raises(LineageError, match="dialect"):
        column_lineage(SQL, "total_amount", "oracle")


def test_sql_lineage_unknown_column():
    with pytest.raises(LineageError, match="could not trace"):
        column_lineage(SQL, "nope", "redshift")
