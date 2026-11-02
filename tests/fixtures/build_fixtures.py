"""Build the synthetic SMX fixture workbooks. All values are invented.

Header names follow the two observed SMX layout families so the reader is exercised on both.
Run: uv run python tests/fixtures/build_fixtures.py
"""

from datetime import datetime
from pathlib import Path

import openpyxl

OUT = Path(__file__).parent / "smx"
FIXED_TS = datetime(2026, 1, 1)


def _sheet(wb, title, header, rows, preamble=None):
    ws = wb.create_sheet(title)
    for line in preamble or []:
        ws.append(line)
    ws.append(header)
    for r in rows:
        ws.append(list(r))


def _save(wb, name, out=None):
    wb.properties.created = FIXED_TS
    wb.properties.modified = FIXED_TS
    out = out or OUT
    out.mkdir(parents=True, exist_ok=True)
    wb.save(out / name)


def build_eip_family():
    """Fleet model in the EIP_SMX_* layout. Seeds one violation per rule."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    _sheet(wb, "Bronze", ["schema", "table name", "source system", "source database/schema", "description"], [
        ("brnz_fleetops", "booking", "fleetops", "fleetops (synthetic)", "Synthetic booking header"),
        ("brnz_fleetops", "meter_change", "fleetops", "fleetops (synthetic)", "Synthetic odometer changes"),
        ("brnz_fleetops", "driver", "fleetops", "fleetops (synthetic)", "Synthetic driver master"),
        ("brnz_fleetops", "vehicle", "fleetops", "fleetops (synthetic)", "Synthetic vehicle master"),
    ])

    stg_header = ["target schema", "target table", "target column name", "data type", "set no.", "source table name", "source column name", "join/rule/filter applied"]
    _sheet(wb, "Silver - Staging", stg_header, [
        ("slvr_stg", "booking", "-", None, 1, "brnz_fleetops.booking", "-", "1:1 mapping from bronze booking"),
        ("slvr_stg", "booking", "booking_num", "string", 1, "brnz_fleetops.booking", "bookingno", "pk"),
        ("slvr_stg", "booking", "driver_num", "string", 1, "brnz_fleetops.booking", "driverno", "1:1 mapping"),
        ("slvr_stg", "booking", "booking_amount", "decimal(18,2)", 1, "brnz_fleetops.booking", "amount", "1:1 mapping"),
        ("slvr_stg", "booking", "booking_date", "date", 1, "brnz_fleetops.booking", "bookdate", "1:1 mapping"),
        ("slvr_stg", "meter_change", "meter_change_num", "string", 1, "brnz_fleetops.meter_change", "changeno", "pk"),
        ("slvr_stg", "meter_change", "booking_num", "string", 1, "brnz_fleetops.meter_change", "bookingno", "1:1 mapping"),
        ("slvr_stg", "meter_change", "meter_reading", "decimal(18,2)", 1, "brnz_fleetops.meter_change", "reading", "1:1 mapping"),
        ("slvr_stg", "driver", "driver_num", "string", 1, "brnz_fleetops.driver", "driverno", "pk"),
        ("slvr_stg", "driver", "driver_nm", "string", 1, "brnz_fleetops.driver", "drivername", "1:1 mapping"),
        ("slvr_stg", "vehicle", "vehicle_num", "string", 1, "brnz_fleetops.vehicle", "vehicleno", "pk"),
        ("slvr_stg", "vehicle", "vehicle_nm", "string", 1, "brnz_fleetops.vehicle", "vehiclename", "1:1 mapping"),
        # LAYER-001 violation: staging sources from gold
        ("slvr_stg", "booking_enriched", "-", None, 1, "gold_fleet.f_booking", "-", "re-reads gold booking"),
        ("slvr_stg", "booking_enriched", "booking_num", "string", 1, "gold_fleet.f_booking", "booking_id", "pk"),
    ])

    idm_header = ["schema", "subject area", "table_name", "column name", "data type", "set no.", "source system", "source table / file", "column", "pk", "scd 2", "rules"]
    _sheet(wb, "Silver - IDM", idm_header, [
        ("slvr_cdm", "fleet", "booking", "-", None, 1, "slvr_stg", "booking, meter_change", "-", None, None, "booking left join meter_change on booking_num"),
        ("slvr_cdm", "fleet", "booking", "booking_id", "string", 1, "slvr_stg", "booking", "booking_num", "Y", "-", "hash(booking_num)"),
        ("slvr_cdm", "fleet", "booking", "booking_num", "string", 1, "slvr_stg", "booking", "booking_num", None, "-", "1:1 mapping"),
        ("slvr_cdm", "fleet", "booking", "driver_id", "string", 1, "slvr_stg", "booking", "driver_num", None, "-", "hash(driver_num)"),
        ("slvr_cdm", "fleet", "booking", "booking_amount", "decimal(18,2)", 1, "slvr_stg", "booking", "booking_amount", None, "-", "1:1 mapping"),
        ("slvr_cdm", "fleet", "booking", "booking_date", "date", 1, "slvr_stg", "booking", "booking_date", None, "-", "1:1 mapping"),
        ("slvr_cdm", "fleet", "booking", "meter_reading", "decimal(18,2)", 1, "slvr_stg", "meter_change", "meter_reading", None, "-", "1:1 mapping"),
        ("slvr_cdm", "fleet", "driver", "driver_id", "string", 1, "slvr_stg", "driver", "driver_num", "Y", "-", "hash(driver_num)"),
        ("slvr_cdm", "fleet", "driver", "driver_nm", "string", 1, "slvr_stg", "driver", "driver_nm", None, "Y", "1:1 mapping"),
        ("slvr_cdm", "fleet", "vehicle", "vehicle_id", "string", 1, "slvr_stg", "vehicle", "vehicle_num", "Y", "-", "hash(vehicle_num)"),
        ("slvr_cdm", "fleet", "vehicle", "vehicle_nm", "string", 1, "slvr_stg", "vehicle", "vehicle_nm", None, "-", "1:1 mapping"),
        # KEY-001 violation: a hash key named with the natural key suffix
        ("slvr_cdm", "fleet", "vehicle", "owner_num", "string", 1, "slvr_stg", "vehicle", "vehicle_num", None, "-", "hash(vehicle_num)"),
    ])

    gold_header = ["schema name", "table name", "column name", "set no.", "pk", "source system", "source table", "source column", "data type", "rules"]
    _sheet(wb, "GOLD - SDM", gold_header, [
        # FACT-001 violation: driver_nm on a fact
        ("gold_fleet", "f_booking", "-", 1, "-", "silver", "slvr_cdm.booking", "-", None, "select from slvr_cdm.booking"),
        ("gold_fleet", "f_booking", "booking_id", 1, "y", "silver", "slvr_cdm.booking", "booking_id", "string", "1:1 mapping"),
        ("gold_fleet", "f_booking", "driver_id", 1, None, "silver", "slvr_cdm.booking", "driver_id", "string", "1:1 mapping"),
        ("gold_fleet", "f_booking", "booking_date", 1, None, "silver", "slvr_cdm.booking", "booking_date", "date", "1:1 mapping"),
        ("gold_fleet", "f_booking", "booking_amount", 1, None, "silver", "slvr_cdm.booking", "booking_amount", "decimal(18,2)", "1:1 mapping"),
        ("gold_fleet", "f_booking", "driver_nm", 1, None, "silver", "slvr_cdm.driver", "driver_nm", "string", "join driver on driver_id"),
        # PARTY-001 violation: data_area_party_id derived in Gold instead of carried forward
        ("gold_fleet", "f_booking", "data_area_party_id", 1, None, "silver", "slvr_cdm.booking", "booking_num", "string", "lookup party by booking_num"),
        # GRAIN-002 violation: only aggregates an existing fact
        ("gold_fleet", "agg_booking_by_driver", "-", 1, "-", "gold", "gold_fleet.f_booking", "-", None, "select driver_id, booking_month, sum(booking_amount) from gold_fleet.f_booking group by driver_id, booking_month"),
        ("gold_fleet", "agg_booking_by_driver", "driver_id", 1, "y", "gold", "gold_fleet.f_booking", "driver_id", "string", "group key"),
        ("gold_fleet", "agg_booking_by_driver", "booking_month", 1, "y", "gold", "gold_fleet.f_booking", "booking_date", "date", "date_trunc month"),
        ("gold_fleet", "agg_booking_by_driver", "total_booking_amount", 1, None, "gold", "gold_fleet.f_booking", "booking_amount", "decimal(18,2)", "sum(booking_amount)"),
        # GRAIN-001 same BU: d_driver and d_driver_profile share grain driver_id
        ("gold_fleet", "d_driver", "driver_id", 1, "y", "silver", "slvr_cdm.driver", "driver_id", "string", "1:1 mapping"),
        ("gold_fleet", "d_driver", "driver_nm", 1, None, "silver", "slvr_cdm.driver", "driver_nm", "string", "1:1 mapping"),
        ("gold_fleet", "d_driver_profile", "driver_id", 1, "y", "silver", "slvr_cdm.driver", "driver_id", "string", "1:1 mapping"),
        ("gold_fleet", "d_driver_profile", "driver_nm", 1, None, "silver", "slvr_cdm.driver", "driver_nm", "string", "1:1 mapping"),
        # GRAIN-001 cross BU: same grain as gold_logistics.d_vehicle
        ("gold_fleet", "d_vehicle", "vehicle_id", 1, "y", "silver", "slvr_cdm.vehicle", "vehicle_id", "string", "1:1 mapping"),
        ("gold_fleet", "d_vehicle", "vehicle_nm", 1, None, "silver", "slvr_cdm.vehicle", "vehicle_nm", "string", "1:1 mapping"),
        # NAME-001 violations: vw_ prefix, no gold prefix, camelCase column
        ("gold_fleet", "vw_booking_recent", "booking_id", 1, "y", "gold", "gold_fleet.f_booking", "booking_id", "string", "filter last 30 days"),
        ("gold_fleet", "vw_booking_recent", "BookingDate", 1, None, "gold", "gold_fleet.f_booking", "booking_date", "date", "1:1 mapping"),
    ])

    _sheet(wb, "Review Comments", ["#", "Gold Table", "Review Comments / Action Required"], [
        (1, "f_booking", "synthetic comment, sheet must be ignored by the reader"),
    ])
    _save(wb, "synthetic_fleet_eip.xlsx")


def build_canonical_family():
    """Logistics model in the canonical A2 layout, including schema-as-header cells."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    _sheet(wb, "Overview", ["Revision History"], [("Version", "Date", "Author", "Comment"), (1, "2026-01-01", "synthetic", "Initial")])

    _sheet(wb, "Bronze", ["Schema Name", "Table Name", "Target Column Name", "Data Type", "Source Table Name", "Source Column Name", "Join/Rule/Filter Applied"], [
        ("brnz_depot", "vehicle_master", "vehicle_code", "String", "vehicle_master", "vehicle_code", "1:1 mapping"),
        ("brnz_depot", "vehicle_master", "vehicle_label", "String", "vehicle_master", "vehicle_label", "1:1 mapping"),
    ])

    common = ["Table Name", "Column Name", "Set No.", "Source System", "Source Table", "Source Column", "Is PK", "Is FK", "Is SCD2", "Rules", "Rules Related Comments", "Data Type"]
    _sheet(wb, "Silver Stage", ["Schema Name", "Table Name", "Column Name", "Set No.", "Source System", "Source Table", "Source Column Name", "Is PK", "Is FK", "Is SCD2", "Rules", "Rules Related Comments", "Sample Values", "Data Type"], [
        ("slvr_stg", "vehicle_master", None, 1, "bronze", "vehicle_master", None, "N", None, None, "1:1 from bronze", None, None, None),
        ("slvr_stg", "vehicle_master", "vehicle_num", 1, "bronze", "vehicle_master", "vehicle_code", "Y", None, None, "1:1 mapping", None, "V001", "string"),
        ("slvr_stg", "vehicle_master", "vehicle_nm", 1, "bronze", "vehicle_master", "vehicle_label", None, None, None, "1:1 mapping", None, "Van A", "string"),
    ])

    _sheet(wb, "Silver - IDM", ["slvr_cdm"] + common, [
        ("slvr_cdm", "vehicle_master", None, 1, "slvr_stg", "vehicle_master", None, None, None, None, "from slvr_stg.vehicle_master", None, None),
        ("slvr_cdm", "vehicle_master", "vehicle_id", 1, "slvr_stg", "vehicle_master", "vehicle_num", "Y", None, None, "hash(vehicle_num)", None, "string"),
        ("slvr_cdm", "vehicle_master", "vehicle_nm", 1, "slvr_stg", "vehicle_master", "vehicle_nm", None, None, None, "1:1 mapping", None, "string"),
    ])

    _sheet(wb, "Gold - SDM", ["slvr_auto"] + common, [
        ("gold_logistics", "d_vehicle", None, 1, "slvr_cdm", "vehicle_master", None, None, None, None, "select from slvr_cdm.vehicle_master", None, None),
        ("gold_logistics", "d_vehicle", "vehicle_id", 1, "slvr_cdm", "vehicle_master", "vehicle_id", "Y", None, None, "1:1 mapping", None, "string"),
        ("gold_logistics", "d_vehicle", "vehicle_nm", 1, "slvr_cdm", "vehicle_master", "vehicle_nm", None, None, None, "1:1 mapping", None, "string"),
    ])
    _save(wb, "synthetic_logistics_canonical.xlsx")


def build_cdm_v1_family():
    """Older CDM v1.0 layout: DocInfo, Bronze - Landing, Lookups, Silver- Staging with marker rows, separate source schema column.
    Kept in its own folder so the default index stays at two models. The reader must still read it; the writer never writes it."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _sheet(wb, "DocInfo", ["Document Information"], [("Model", "synthetic address"), ("Version", "1.0")])
    _sheet(wb, "Bronze - Landing", ["Target Table Name", "Source System", "Source Table Name/File Name", "Join/Rule/Filter Applied"], [
        ("address", "crm", "address_extract", "1:1 load"),
    ])
    _sheet(wb, "Lookups", ["Subject Area", "Schema Name", "Table_Name", "Column Name", "SET", "Rules", "Rule Related Comments"], [
        ("party", "slvr_cdm", "address_type", "address_type_cd", 1, "static list", "synthetic, sheet must be ignored by the reader"),
    ])
    _sheet(wb, "Silver- Staging", ["Target Column Name", "Description", "Data Type", "SET NO.", "Source Table Name", "Source Column Name", "Join/Rule/Filter Applied"], [
        ("slvr_stg.address", None, None, 1, "address", None, "from bronze address"),
        ("address_num", "Address number", "string", 1, "address", "addr_no", "pk"),
        ("city_nm", "City", "string", 1, "address", "city", "1:1 mapping"),
    ])
    _sheet(wb, "Silver - IDM", ["Subject Area", "Schema Name", "Table_Name", "Column Name", "SET NO.", "Source Schema Name", "Table / File", "Column", "PK", "SCD2", "MC", "MnC", "Rules", "Rule Related Comments"], [
        ("party", "slvr_cdm", "address", "address_id", 1, "slvr_stg", "address", "address_num", "Y", None, None, None, "hash(address_num)", None),
        ("party", "slvr_cdm", "address", "city_nm", 1, "slvr_stg", "address", "city_nm", None, "Y", None, None, "1:1 mapping", None),
    ])
    _sheet(wb, "Gold - SDM", ["Schema Name", "Table_Name", "Column Name", "SET NO.", "Source Schema Name", "Table / File", "Column", "Rules"], [
        ("gold_party", "d_address", "address_id", 1, "slvr_cdm", "address", "address_id", "1:1 mapping"),
        ("gold_party", "d_address", "city_nm", 1, "slvr_cdm", "address", "city_nm", "1:1 mapping"),
    ])
    _save(wb, "synthetic_cdm_v1.xlsx", OUT.parent / "smx_cdm_v1")


if __name__ == "__main__":
    build_eip_family()
    build_canonical_family()
    build_cdm_v1_family()
    print("wrote", sorted(p.name for p in OUT.glob("*.xlsx")))
