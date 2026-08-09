# master_sheet.py
"""
Persistent workbook consolidating carrier-tracking status and Send-to-Amazon
workflow/delivery-window state, one row per FBA ID. Rewritten in full from an
in-memory dict on every save (same convention as tracking_status.py's status
cache), so updating a shipment's row is just: load, mutate the dict entry for
that FBA ID, save.
"""
from pathlib import Path

MASTER_SHEET_COLUMNS = [
    "Tracking Status", "Delivery Date Status", "Tracking Number", "Carrier",
    "FBA ID", "Shipment Name", "Destination", "Ctns", "Shipping Way",
    "Notes (source)", "Label Created Date", "Expected Delivery Date",
    "Current Status", "Last Checked", "Region", "Workflow ID",
]

# Maps in-memory dict keys to their column position/header above, in column order.
_FIELD_ORDER = [
    ("tracking_status", "Tracking Status"),
    ("delivery_date_status", "Delivery Date Status"),
    ("tracking", "Tracking Number"),
    ("carrier", "Carrier"),
    ("fba_id", "FBA ID"),
    ("name", "Shipment Name"),
    ("destination", "Destination"),
    ("ctns", "Ctns"),
    ("shipping_way", "Shipping Way"),
    ("notes", "Notes (source)"),
    ("label_created_date", "Label Created Date"),
    ("expected_delivery_date", "Expected Delivery Date"),
    ("status", "Current Status"),
    ("last_checked", "Last Checked"),
    ("region", "Region"),
    ("workflow_id", "Workflow ID"),
]


def load_master_sheet(path: str) -> dict:
    """Reads the persistent master workbook into {fba_id: {field: value}}."""
    if not Path(path).exists():
        return {}
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    sheet = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[4]:  # FBA ID column
            continue
        entry = {}
        for i, (key, _header) in enumerate(_FIELD_ORDER):
            value = row[i] if i < len(row) else None
            entry[key] = value if value is not None else ""
        sheet[str(entry["fba_id"]).strip()] = entry
    return sheet


def save_master_sheet(path: str, sheet: dict) -> None:
    """Rewrites the whole master workbook from the in-memory sheet dict, sorted by FBA ID."""
    import openpyxl

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Shipment Tracking Master"
    ws.append(MASTER_SHEET_COLUMNS)
    for fba_id in sorted(sheet.keys()):
        entry = sheet[fba_id]
        ws.append([entry.get(key, "") for key, _header in _FIELD_ORDER])
    wb.save(path)
