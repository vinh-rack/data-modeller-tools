import pytest

from dma.classify import SchemaClassifier
from dma.index.model_index import ModelIndex
from dma.models import Column, Layer, SourceRef, Table
from dma.rules import REGISTRY, RuleContext, run_rules
from dma.rules.base import rule_config
from dma.rules.fact_keys_only import check_fact_keys_only
from dma.rules.gold_columns_used import check_gold_columns_used
from dma.rules.layer_hierarchy import check_layer_hierarchy
from dma.rules.naming import check_naming
from dma.rules.one_table_per_grain import check_aggregate_should_be_view, check_one_table_per_grain


def tbl(qualified, layer, columns=(), sources=(), rules_text="", bu=None, model="m"):
    schema, name = qualified.split(".")
    cols = [c if isinstance(c, Column) else Column(name=c) for c in columns]
    srcs = [SourceRef(schema_name=s.split(".")[0], table=s.split(".")[1]) for s in sources]
    return Table(model=model, layer=layer, schema_name=schema, name=name, columns=cols, sources=srcs, rules_text=rules_text, bu=bu, sheet="s", row=2)


def pk(name):
    return Column(name=name, is_pk=True)


def run(fn, rules_cfg, tables, targets=None, usage=None):
    idx = ModelIndex({}, tables, SchemaClassifier(rules_cfg))
    ctx = RuleContext(index=idx, targets=targets if targets is not None else tables, config=rules_cfg, column_usage=usage)
    return fn(ctx, rule_config(rules_cfg, fn.rule_id))


def ids(findings, severity=None):
    return [f.rule_id for f in findings if severity is None or f.severity == severity]


class TestLayer001:
    def test_pass_upstream_and_same_layer(self, rules_cfg):
        t = tbl("gold_fleet.f_x", Layer.GOLD, ["a"], ["slvr_cdm.x", "gold_fleet.d_y"])
        assert run(check_layer_hierarchy, rules_cfg, [t]) == []

    def test_fail_downstream_source(self, rules_cfg):
        t = tbl("slvr_stg.x", Layer.SILVER_STAGING, [Column(name="a", source_table="gold_fleet.f_x")], ["gold_fleet.f_x"])
        f = run(check_layer_hierarchy, rules_cfg, [t])
        assert ids(f, "error") == ["LAYER-001"]
        assert f[0].evidence["source_layer"] == "gold" and f[0].column == "a"

    def test_unresolved_source_is_info(self, rules_cfg):
        t = tbl("slvr_cdm.x", Layer.SILVER_IDM, ["a"], ["mystery.x"])
        assert ids(run(check_layer_hierarchy, rules_cfg, [t]), "info") == ["LAYER-001"]


class TestFact001:
    def test_pass_keys_and_measures(self, rules_cfg):
        t = tbl("gold_fleet.f_booking", Layer.GOLD, [pk("booking_id"), "driver_id", "booking_amount"])
        assert run(check_fact_keys_only, rules_cfg, [t]) == []

    def test_fail_descriptive_attribute(self, rules_cfg):
        t = tbl("gold_fleet.f_booking", Layer.GOLD, [pk("booking_id"), "driver_nm"])
        f = run(check_fact_keys_only, rules_cfg, [t])
        assert [(x.rule_id, x.column) for x in f] == [("FACT-001", "driver_nm")]

    def test_dimension_is_not_checked(self, rules_cfg):
        t = tbl("gold_fleet.d_driver", Layer.GOLD, [pk("driver_id"), "driver_nm"])
        assert run(check_fact_keys_only, rules_cfg, [t]) == []


class TestName001:
    def test_pass(self, rules_cfg):
        t = tbl("gold_fleet.f_inventory_vw", Layer.GOLD, [pk("invent_trans_id"), "quantity"])
        assert run(check_naming, rules_cfg, [t]) == []

    def test_fail_prefix_view_and_case(self, rules_cfg):
        t = tbl("gold_fleet.vw_booking", Layer.GOLD, [pk("booking_id"), "BookingDate"])
        checks = sorted(f.evidence["check"] for f in run(check_naming, rules_cfg, [t]))
        assert checks == ["column_snake_case", "gold_prefix", "view_suffix"]

    def test_fail_forbidden_suffix(self, rules_cfg):
        t = tbl("slvr_cdm.settlement_lookup", Layer.SILVER_IDM, [pk("settlement_id")])
        assert [f.evidence["check"] for f in run(check_naming, rules_cfg, [t])] == ["forbidden_suffix"]

    def test_silver_has_no_prefix_requirement(self, rules_cfg):
        t = tbl("slvr_cdm.booking", Layer.SILVER_IDM, [pk("booking_id")])
        assert run(check_naming, rules_cfg, [t]) == []


class TestGold001:
    def test_pass_all_columns_used(self, rules_cfg):
        t = tbl("gold_fleet.f_booking", Layer.GOLD, [pk("booking_id"), "booking_amount"])
        usage = ["gold_fleet.f_booking.booking_id", "f_booking.booking_amount"]
        assert run(check_gold_columns_used, rules_cfg, [t], usage=set(usage)) == []

    def test_fail_unused_column(self, rules_cfg):
        t = tbl("gold_fleet.f_booking", Layer.GOLD, [pk("booking_id"), "spare_col"])
        f = run(check_gold_columns_used, rules_cfg, [t], usage={"gold_fleet.f_booking.booking_id"})
        assert [(x.rule_id, x.column) for x in f] == [("GOLD-001", "spare_col")]

    def test_skipped_without_usage(self, rules_cfg):
        t = tbl("gold_fleet.f_booking", Layer.GOLD, [pk("booking_id")])
        f = run(check_gold_columns_used, rules_cfg, [t], usage=None)
        assert ids(f, "info") == ["GOLD-001"] and f[0].evidence["status"] == "skipped"


class TestGrain001:
    def test_pass_distinct_grain(self, rules_cfg):
        a = tbl("gold_fleet.d_driver", Layer.GOLD, [pk("driver_id")], bu="fleet")
        b = tbl("gold_fleet.d_vehicle", Layer.GOLD, [pk("vehicle_id")], bu="fleet")
        assert run(check_one_table_per_grain, rules_cfg, [a, b]) == []

    def test_fail_same_grain_same_bu(self, rules_cfg):
        a = tbl("gold_fleet.d_driver", Layer.GOLD, [pk("driver_id")], bu="fleet")
        b = tbl("gold_fleet.d_driver_profile", Layer.GOLD, [pk("driver_id")], bu="fleet")
        f = run(check_one_table_per_grain, rules_cfg, [a, b])
        assert ids(f, "error") == ["GRAIN-001"]

    def test_cross_bu_flags_cdm_promotion(self, rules_cfg):
        a = tbl("gold_fleet.d_vehicle", Layer.GOLD, [pk("vehicle_id")], bu="fleet")
        b = tbl("gold_logistics.d_vehicle", Layer.GOLD, [pk("vehicle_id")], bu="logistics")
        f = run(check_one_table_per_grain, rules_cfg, [a, b], targets=[a])
        assert ids(f, "warning") == ["GRAIN-001"] and "CDM promotion" in f[0].message

    def test_other_layer_is_not_compared(self, rules_cfg):
        a = tbl("gold_fleet.d_vehicle", Layer.GOLD, [pk("vehicle_id")], bu="fleet")
        b = tbl("slvr_cdm.vehicle", Layer.SILVER_IDM, [pk("vehicle_id")], bu="cdm")
        assert run(check_one_table_per_grain, rules_cfg, [a, b], targets=[a]) == []


class TestGrain002:
    fact = staticmethod(lambda: tbl("gold_fleet.f_booking", Layer.GOLD, [pk("booking_id"), "driver_id", "booking_amount"], ["slvr_cdm.booking"]))

    def test_pass_new_grain_from_silver(self, rules_cfg):
        t = tbl("gold_fleet.f_trip", Layer.GOLD, [pk("trip_id")], ["slvr_cdm.trip"], rules_text="sum(distance) group by trip_id")
        assert run(check_aggregate_should_be_view, rules_cfg, [self.fact(), t], targets=[t]) == []

    def test_pass_already_a_view(self, rules_cfg):
        t = tbl("gold_fleet.agg_booking_vw", Layer.GOLD, [pk("driver_id")], ["gold_fleet.f_booking"], rules_text="sum(booking_amount) group by driver_id")
        assert run(check_aggregate_should_be_view, rules_cfg, [self.fact(), t], targets=[t]) == []

    def test_fail_aggregate_of_existing_fact(self, rules_cfg):
        t = tbl("gold_fleet.agg_booking_by_driver", Layer.GOLD, [pk("driver_id")], ["gold_fleet.f_booking"], rules_text="select driver_id, sum(booking_amount) group by driver_id")
        f = run(check_aggregate_should_be_view, rules_cfg, [self.fact(), t], targets=[t])
        assert ids(f, "warning") == ["GRAIN-002"] and f[0].evidence["parent_fact"] == "gold_fleet.f_booking"


def test_every_rule_fires_on_fixture(index, rules_cfg):
    targets = index.get_model("synthetic_fleet_eip")
    usage = {f"{t.qualified}.{c.name}" for t in targets for c in t.columns if c.name != "booking_date"}
    findings = run_rules(RuleContext(index=index, targets=targets, config=rules_cfg, column_usage=usage))
    fired = {f.rule_id for f in findings if f.severity != "info"}
    assert fired == set(REGISTRY) == {"LAYER-001", "FACT-001", "NAME-001", "GOLD-001", "GRAIN-001", "GRAIN-002"}
    for f in findings:
        assert f.rule_id and f.severity and f.table and f.message and isinstance(f.evidence, dict)


def test_disabled_rule_is_skipped(index, rules_cfg):
    cfg = {**rules_cfg, "rules": {**rules_cfg["rules"], "NAME-001": {"enabled": False}}}
    findings = run_rules(RuleContext(index=index, targets=index.tables, config=cfg))
    assert "NAME-001" not in {f.rule_id for f in findings}


@pytest.mark.parametrize("rule_id", sorted(REGISTRY))
def test_rule_has_config_entry(rules_cfg, rule_id):
    assert rule_id in rules_cfg["rules"]
