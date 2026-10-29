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


# Table-level lineage diagrams from the SMX index.

from dma.index.model_index import ModelIndex  # noqa: E402
from dma.kb import ReportEntry, TableMap  # noqa: E402
from dma.lineage.diagram import report_lineage, table_lineage  # noqa: E402
from dma.models import SourceRef  # noqa: E402


def edges(g):
    return {(e.source, e.target) for e in g.edges}


def test_upstream_reaches_bronze(index):
    g = table_lineage(index, "gold_fleet.f_booking")
    assert ("brnz_fleetops.meter_change", "slvr_stg.meter_change") in edges(g) and ("slvr_cdm.driver", "gold_fleet.f_booking") in edges(g)
    assert all(n.status == "confirmed" for n in g.nodes) and g.root == ["gold_fleet.f_booking"]
    assert g.mermaid.startswith("flowchart LR\n") and "subgraph bronze [Bronze]" in g.mermaid


def test_bare_source_names_follow_the_source_layer(index):
    # vehicle_master exists in Bronze, Silver Staging and Silver IDM; each must link only to its own upstream.
    assert edges(table_lineage(index, "gold_logistics.d_vehicle")) == {
        ("brnz_depot.vehicle_master", "slvr_stg.vehicle_master"),
        ("slvr_stg.vehicle_master", "slvr_cdm.vehicle_master"),
        ("slvr_cdm.vehicle_master", "gold_logistics.d_vehicle"),
    }


def test_downstream_and_both(index):
    down = edges(table_lineage(index, "brnz_fleetops.booking", "downstream"))
    assert ("gold_fleet.f_booking", "gold_fleet.agg_booking_by_driver") in down and not any(t.startswith("brnz_") for _, t in down)
    both = table_lineage(index, "slvr_cdm.driver", "both")
    assert ("brnz_fleetops.driver", "slvr_stg.driver") in edges(both) and ("slvr_cdm.driver", "gold_fleet.d_driver") in edges(both)


def test_unindexed_source_is_inferred_and_unknown_table_refused(index):
    t = index.resolve("slvr_stg.booking")[0]
    orphan = t.model_copy(update={"sources": [SourceRef(schema_name="slvr_cdm", table="not_modelled")], "name": "orphan"})
    g = table_lineage(ModelIndex(index.models, [*index.tables, orphan], index.classifier), "slvr_stg.orphan")
    assert [(n.table, n.status) for n in g.nodes if n.status != "confirmed"] == [("slvr_cdm.not_modelled", "inferred")]
    assert "class n" in g.mermaid and "inferred" in g.mermaid
    with pytest.raises(LineageError):
        table_lineage(index, "nope.nope")


def test_report_lineage_traces_or_marks_unknown(index):
    entry = ReportEntry(report_id="r1", name="Fleet", tables={
        "Booking": TableMap(table="gold_fleet.f_booking"),
        "Driver": TableMap(table="gold_fleet.d_driver"),
        "Staging": TableMap(table="slvr_stg.booking"),
        "Gone": TableMap(table="gold_fleet.d_gone"),
    })
    r = report_lineage(index, entry, ["Booking", "Driver", "Calendar", "Staging"])
    rows = {x.pbi_table: x for x in r.tables}
    assert rows["Booking"].status == "confirmed" and rows["Booking"].bronze == ["brnz_fleetops.booking", "brnz_fleetops.driver", "brnz_fleetops.meter_change"]
    assert rows["Driver"].bronze == ["brnz_fleetops.driver"]
    assert {k: rows[k].note for k in ("Calendar", "Staging", "Gone")} == {
        "Calendar": "not in the report map",
        "Staging": "mapped to slvr_stg.booking, which is not a Gold table in any SMX",
        "Gone": "in the report map but not in the Power BI model",
    }
    assert "subgraph other [Power BI: Fleet]" in r.mermaid and "report.Booking" in r.mermaid
