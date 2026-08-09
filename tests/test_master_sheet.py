import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from master_sheet import (
    MASTER_SHEET_COLUMNS,
    load_master_sheet,
    save_master_sheet,
)


@pytest.mark.unit
def test_master_sheet_columns_order():
    assert MASTER_SHEET_COLUMNS == [
        "Tracking Status", "Delivery Date Status", "Tracking Number", "Carrier",
        "FBA ID", "Shipment Name", "Destination", "Ctns", "Shipping Way",
        "Notes (source)", "Label Created Date", "Expected Delivery Date",
        "Current Status", "Last Checked", "Region", "Workflow ID",
    ]


@pytest.mark.unit
def test_load_master_sheet_missing_file_returns_empty_dict(tmp_path):
    path = str(tmp_path / "does_not_exist.xlsx")
    assert load_master_sheet(path) == {}


@pytest.mark.unit
def test_save_then_load_round_trip(tmp_path):
    path = str(tmp_path / "master.xlsx")
    sheet = {
        "FBA001": {
            "tracking_status": "pending",
            "delivery_date_status": "pending",
            "tracking": "1Z001",
            "carrier": "UPS",
            "fba_id": "FBA001",
            "name": "Widget Pack",
            "destination": "ORF2",
            "ctns": 9,
            "shipping_way": "express",
            "notes": "",
            "label_created_date": "2026-06-01",
            "expected_delivery_date": "2026-06-10",
            "status": "In Transit",
            "last_checked": "2026-06-05 10:00",
            "region": "US",
            "workflow_id": "wf-abc-123",
        },
    }
    save_master_sheet(path, sheet)
    loaded = load_master_sheet(path)
    assert loaded == sheet


@pytest.mark.unit
def test_save_master_sheet_writes_columns_in_order(tmp_path):
    path = str(tmp_path / "master.xlsx")
    sheet = {
        "FBA001": {
            "tracking_status": "updated", "delivery_date_status": "pending",
            "tracking": "1Z001", "carrier": "UPS", "fba_id": "FBA001",
            "name": "Widget", "destination": "ORF2", "ctns": 1,
            "shipping_way": "express", "notes": "", "label_created_date": "",
            "expected_delivery_date": "", "status": "Delivered",
            "last_checked": "2026-06-05 10:00", "region": "US",
            "workflow_id": "wf-abc-123",
        },
    }
    save_master_sheet(path, sheet)

    import openpyxl
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    header = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    assert header == MASTER_SHEET_COLUMNS
    data_row = [cell.value for cell in next(ws.iter_rows(min_row=2, max_row=2))]
    # openpyxl reads back an empty-string cell as None -- load_master_sheet
    # normalizes this back to "" for callers; this test checks raw cell values.
    assert data_row == [
        "updated", "pending", "1Z001", "UPS", "FBA001", "Widget", "ORF2", 1,
        "express", None, None, None, "Delivered", "2026-06-05 10:00", "US", "wf-abc-123",
    ]


@pytest.mark.unit
def test_save_master_sheet_overwrites_existing_row_for_same_fba_id(tmp_path):
    path = str(tmp_path / "master.xlsx")
    first = {
        "FBA001": {
            "tracking_status": "pending", "delivery_date_status": "pending",
            "tracking": "1Z001", "carrier": "UPS", "fba_id": "FBA001",
            "name": "Widget", "destination": "ORF2", "ctns": 1,
            "shipping_way": "express", "notes": "", "label_created_date": "",
            "expected_delivery_date": "", "status": "In Transit",
            "last_checked": "2026-06-01 09:00", "region": "US",
            "workflow_id": "",
        },
    }
    save_master_sheet(path, first)

    updated = load_master_sheet(path)
    updated["FBA001"]["tracking_status"] = "updated"
    updated["FBA001"]["workflow_id"] = "wf-xyz"
    save_master_sheet(path, updated)

    reloaded = load_master_sheet(path)
    assert len(reloaded) == 1
    assert reloaded["FBA001"]["tracking_status"] == "updated"
    assert reloaded["FBA001"]["workflow_id"] == "wf-xyz"


@pytest.mark.unit
def test_save_master_sheet_multiple_rows_sorted_by_fba_id(tmp_path):
    path = str(tmp_path / "master.xlsx")

    def entry(fba_id):
        return {
            "tracking_status": "pending", "delivery_date_status": "pending",
            "tracking": "1Z", "carrier": "UPS", "fba_id": fba_id,
            "name": "", "destination": "", "ctns": "", "shipping_way": "",
            "notes": "", "label_created_date": "", "expected_delivery_date": "",
            "status": "", "last_checked": "", "region": "US", "workflow_id": "",
        }

    sheet = {"FBA002": entry("FBA002"), "FBA001": entry("FBA001")}
    save_master_sheet(path, sheet)

    import openpyxl
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    fba_ids = [row[4] for row in ws.iter_rows(min_row=2, values_only=True)]
    assert fba_ids == ["FBA001", "FBA002"]
