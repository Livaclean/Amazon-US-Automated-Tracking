import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import openpyxl
from pathlib import Path
from openpyxl.styles import PatternFill

def test_highlight_rows_applies_yellow_fill(tmp_path):
    from highlight_excel import highlight_and_save

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["A", "B", "C", "D_fc", "E_fba", "F", "G", "H_tracking", "I_carrier"])
    ws.append([None, None, None, "BNA1", "FBA001", None, None, "1Z123", "UPS"])
    ws.append([None, None, None, "BNA1", "FBA002", None, None, "1Z456", "UPS"])
    ws.append([None, None, None, "BNA1", "FBA003", None, None, "1ZABC", "UPS"])
    src = tmp_path / "test.xlsx"
    wb.save(src)

    updated_rows = {2, 4}  # 1-indexed Excel rows
    dest = tmp_path / "output.xlsx"

    highlight_and_save(str(src), str(dest), updated_rows)

    result = openpyxl.load_workbook(dest)
    ws2 = result.active
    yellow = "FFFF00"
    # Check that highlighted rows have yellow fill on at least the first cell
    assert ws2.cell(2, 1).fill.fgColor.rgb[-6:] == yellow  # FBA001 highlighted
    assert ws2.cell(3, 1).fill.fgColor.rgb[-6:] != yellow  # FBA002 not highlighted
    assert ws2.cell(4, 1).fill.fgColor.rgb[-6:] == yellow  # FBA003 highlighted

def test_highlight_saves_to_xlsx_for_xls_dest(tmp_path):
    """When dest has .xls extension, output should be saved as .xlsx instead."""
    from highlight_excel import highlight_and_save
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["header"])
    ws.append(["data"])
    src = tmp_path / "test.xlsx"
    wb.save(src)
    dest = tmp_path / "output.xls"  # request .xls output
    result_path = highlight_and_save(str(src), str(dest), {2})
    assert result_path.endswith(".xlsx")
    assert Path(result_path).exists()

def test_highlight_xls_source(sample_xls, tmp_path):
    """highlight_and_save should handle .xls source, output as .xlsx."""
    from highlight_excel import highlight_and_save
    import openpyxl
    dest = tmp_path / "output.xlsx"
    result_path = highlight_and_save(sample_xls, str(dest), {2, 3})
    assert result_path.endswith(".xlsx")
    assert Path(result_path).exists()
    wb = openpyxl.load_workbook(result_path)
    assert wb.active is not None
