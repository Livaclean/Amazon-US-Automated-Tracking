import pytest
import os
import sys
import logging
import xlrd
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from carton_tracking import (
    parse_carton_tracking_cell,
    detect_carton_tracking_column,
    build_carton_tracking_map,
)


# ---------------------------------------------------------------------------
# parse_carton_tracking_cell
# ---------------------------------------------------------------------------

def test_parse_empty_cell_returns_empty_list():
    assert parse_carton_tracking_cell("") == []
    assert parse_carton_tracking_cell(None) == []
    assert parse_carton_tracking_cell("   ") == []


def test_parse_single_carton_entry():
    text = "1ZK6B4420338604208-FBA19L9DHD1SU000001"
    result = parse_carton_tracking_cell(text)
    assert result == [{"fba_id": "FBA19L9DHD1S", "seq": 1, "tracking": "1ZK6B4420338604208"}]


def test_parse_multi_line_multi_carton_entries():
    text = (
        "1ZK6B4420336908189-FBA19L2VH5XKU000001\n"
        "1ZK6B4420335331393-FBA19L4ZZS14U000001\n"
        "1ZK6B4420338604208-FBA19L9DHD1SU000001\n"
        "1ZK6B4420322022616-FBA19L9DHD1SU000002"
    )
    result = parse_carton_tracking_cell(text)
    assert result == [
        {"fba_id": "FBA19L2VH5XK", "seq": 1, "tracking": "1ZK6B4420336908189"},
        {"fba_id": "FBA19L4ZZS14", "seq": 1, "tracking": "1ZK6B4420335331393"},
        {"fba_id": "FBA19L9DHD1S", "seq": 1, "tracking": "1ZK6B4420338604208"},
        {"fba_id": "FBA19L9DHD1S", "seq": 2, "tracking": "1ZK6B4420322022616"},
    ]


def test_parse_fedex_style_tracking_number():
    text = "885868822164-FBA19KQL1XTBU000003"
    result = parse_carton_tracking_cell(text)
    assert result == [{"fba_id": "FBA19KQL1XTB", "seq": 3, "tracking": "885868822164"}]


def test_parse_returns_none_for_multiple_entries_crammed_on_one_line():
    """Real-world malformed variant: two entries on one line, reversed order,
    no newline separator — must not be guessed, per user decision to fall back
    to carrier scrape on anything ambiguous."""
    text = "1ZA6D6320410956580 --FBA19KXC9R76U000001   FBA19KXC9R76U000002-1ZA6D6320406750998"
    assert parse_carton_tracking_cell(text) is None


def test_parse_returns_none_for_tracking_without_fba_token():
    assert parse_carton_tracking_cell("1ZK6B4420338604208") is None


def test_parse_returns_none_for_fba_token_without_tracking():
    assert parse_carton_tracking_cell("FBA19L9DHD1SU000001") is None


# ---------------------------------------------------------------------------
# detect_carton_tracking_column
# ---------------------------------------------------------------------------

class _FakeXlrdCell:
    def __init__(self, value):
        self.value = value
        self.ctype = xlrd.XL_CELL_NUMBER if isinstance(value, (int, float)) else xlrd.XL_CELL_TEXT


class _FakeXlrdSheet:
    def __init__(self, name, rows):
        self.name = name
        self._rows = rows
        self.nrows = len(rows)
        self.ncols = max((len(r) for r in rows), default=0)

    def cell(self, r, c):
        row = self._rows[r]
        return _FakeXlrdCell(row[c] if c < len(row) else "")


def test_detect_carton_tracking_column_finds_trailing_blob_column():
    header = ["SYSTEM NO", "Order No", "ITEMS", "DESTINATION", "FBA ID",
              "NO OF CTNS ", "SHIPPING  WAY", "TRACKING NUMBERS", "CARRIER", "ETD", "", ""]
    row1 = ["A1", "Item", "", "ORF2", "FBA19KXC9R76", 2, "express",
            "1ZA6D7510412465060", "UPS", "", "delivered on 2026.02.24", ""]
    row2 = ["A2", "Item", "", "IMI1", "FBA19L9DHD1S", 2, "C-SEA",
            "1ZK6B4420336908189", "UPS", "", "",
            "1ZK6B4420338604208-FBA19L9DHD1SU000001\n1ZK6B4420322022616-FBA19L9DHD1SU000002"]
    sheet = _FakeXlrdSheet("US", [header, row1, row2])
    assert detect_carton_tracking_column(sheet, header_row=0, exclude_cols={3, 4, 7, 8, 1, 5, 6}) == 11


def test_detect_carton_tracking_column_returns_none_when_absent():
    header = ["SYSTEM NO", "Order No-ITEMS", "DESTINATION", "FBA ID",
              "NO OF CTNS ", "SHIPPING  WAY", "TRACKING NUMBERS", "CARRIER", "ETD", "ETAs"]
    row1 = ["A1", "Widget DE", "DMT2", "FBA15KK5TKDF", 4, "C-AIR",
            "1ZC51W066825252132", "ups", "", "delivered on 7.31"]
    sheet = _FakeXlrdSheet("DE", [header, row1])
    assert detect_carton_tracking_column(sheet, header_row=0, exclude_cols={2, 3, 6, 7, 1, 4, 5}) is None


# ---------------------------------------------------------------------------
# build_carton_tracking_map
# ---------------------------------------------------------------------------

class _FakeXlrdBook:
    def __init__(self, sheets):
        self._sheets = sheets
        self.nsheets = len(sheets)

    def sheet_by_index(self, i):
        return self._sheets[i]


_US_HEADER = ["SYSTEM NO", "Order No", "ITEMS", "DESTINATION", "FBA ID",
              "NO OF CTNS ", "SHIPPING  WAY", "TRACKING NUMBERS", "CARRIER", "ETD", "", ""]


def test_build_carton_tracking_map_filters_blob_to_rows_own_fba_id(monkeypatch, tmp_path):
    """One shared blob lists cartons for two different destination FBA IDs —
    each row must only get the tracking numbers for its OWN FBA ID, in carton order."""
    row1 = ["A1", "Item", "", "IMI1", "FBA19L9DHD1S", 2, "C-SEA", "1ZK6B4420336908189", "UPS", "", "",
            "1ZK6B4420336908189-FBA19L2VH5XKU000001\n"
            "1ZK6B4420338604208-FBA19L9DHD1SU000001\n"
            "1ZK6B4420322022616-FBA19L9DHD1SU000002"]
    row2 = ["A2", "Item", "", "IMI1", "FBA19L2VH5XK", 1, "C-SEA", "1ZK6B4420336908189", "UPS", "", "",
            "1ZK6B4420336908189-FBA19L2VH5XKU000001\n"
            "1ZK6B4420338604208-FBA19L9DHD1SU000001\n"
            "1ZK6B4420322022616-FBA19L9DHD1SU000002"]
    sheet = _FakeXlrdSheet("US", [_US_HEADER, row1, row2])
    wb = _FakeXlrdBook([sheet])
    monkeypatch.setattr(xlrd, "open_workbook", lambda path: wb)

    result = build_carton_tracking_map([str(tmp_path / "shipments.xls")], {})

    assert result[("FBA19L9DHD1S", 2)] == ["1ZK6B4420338604208", "1ZK6B4420322022616"]
    assert result[("FBA19L2VH5XK", 3)] == ["1ZK6B4420336908189"]


def test_build_carton_tracking_map_skips_rows_with_no_matching_blob_entry(monkeypatch, tmp_path):
    row1 = ["A1", "Item", "", "IMI1", "FBA_NOT_IN_BLOB", 1, "C-SEA", "1Z001", "UPS", "", "",
            "1ZK6B4420338604208-FBA19L9DHD1SU000001"]
    sheet = _FakeXlrdSheet("US", [_US_HEADER, row1])
    wb = _FakeXlrdBook([sheet])
    monkeypatch.setattr(xlrd, "open_workbook", lambda path: wb)

    result = build_carton_tracking_map([str(tmp_path / "shipments.xls")], {})

    assert ("FBA_NOT_IN_BLOB", 2) not in result


def test_build_carton_tracking_map_skips_rows_with_malformed_blob(monkeypatch, tmp_path, caplog):
    row1 = ["A1", "Item", "", "IMI1", "FBA19KXC9R76", 1, "express", "1ZA6D7510412465060", "UPS", "", "",
            "1ZA6D6320410956580 --FBA19KXC9R76U000001   FBA19KXC9R76U000002-1ZA6D6320406750998"]
    sheet = _FakeXlrdSheet("US", [_US_HEADER, row1])
    wb = _FakeXlrdBook([sheet])
    monkeypatch.setattr(xlrd, "open_workbook", lambda path: wb)

    with caplog.at_level(logging.WARNING):
        result = build_carton_tracking_map([str(tmp_path / "shipments.xls")], {})

    assert ("FBA19KXC9R76", 2) not in result


def test_build_carton_tracking_map_empty_when_no_cartons_column(monkeypatch, tmp_path):
    """DE-shaped sheet: no trailing carton-tracking column at all."""
    header = ["SYSTEM NO", "Order No-ITEMS", "DESTINATION", "FBA ID",
              "NO OF CTNS ", "SHIPPING  WAY", "TRACKING NUMBERS", "CARRIER", "ETD", "ETAs"]
    row1 = ["A1", "Widget DE", "DMT2", "FBA15KK5TKDF", 4, "C-AIR",
            "1ZC51W066825252132", "ups", "", "delivered on 7.31"]
    sheet = _FakeXlrdSheet("DE", [header, row1])
    wb = _FakeXlrdBook([sheet])
    monkeypatch.setattr(xlrd, "open_workbook", lambda path: wb)

    result = build_carton_tracking_map([str(tmp_path / "shipments.xls")], {})

    assert result == {}


def test_build_carton_tracking_map_splits_slash_combined_fba_id(monkeypatch, tmp_path):
    """FBA ID cell 'STAR-A/STAR-B' style splitting mirrors group_by_fba_id()'s behavior."""
    row1 = ["A1", "Item", "", "IMI1", "FBAAAA111111/FBABBB222222", 1, "C-SEA", "1Z001", "UPS", "", "",
            "1ZK6B4420338604208-FBAAAA111111U000001\n1ZK6B4420322022616-FBABBB222222U000001"]
    sheet = _FakeXlrdSheet("US", [_US_HEADER, row1])
    wb = _FakeXlrdBook([sheet])
    monkeypatch.setattr(xlrd, "open_workbook", lambda path: wb)

    result = build_carton_tracking_map([str(tmp_path / "shipments.xls")], {})

    assert result[("FBAAAA111111", 2)] == ["1ZK6B4420338604208"]
    assert result[("FBABBB222222", 2)] == ["1ZK6B4420322022616"]
