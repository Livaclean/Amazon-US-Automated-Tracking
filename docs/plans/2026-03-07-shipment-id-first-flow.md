# Shipment-ID-First Flow with Highlighting Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor the pipeline to check shipment IDs first (identifying which are missing tracking), record both categories to plain text files, skip tracking numbers containing "/", and highlight updated rows in the output Excel file.

**Architecture:** Parse ALL FBA IDs from Excel (including those with empty tracking), categorize into missing vs. has-tracking, write both lists to plain-text record files, filter out "/" tracking entries, then after upload apply openpyxl cell highlighting to rows that were updated before saving to output folder.

**Tech Stack:** Python, openpyxl (highlight + write xlsx), xlrd (read .xls), existing Playwright upload logic.

---

## Current Flow (for reference)

```
Excel → filter rows WITH tracking → group by FBA ID → carrier scrape → Amazon upload → copy file to output
```

## New Flow

```
Excel → load ALL FBA IDs (US FC filter) → split: missing_tracking vs has_tracking
      → write two .txt record files (FBA IDs only)
      → skip tracking entries containing "/"
      → carrier scrape (or skip) → Amazon upload
      → highlight updated rows in Excel → save highlighted file to output
```

---

### Task 1: Update parse_excel.py to load ALL FBA IDs and add "/" filter

**Files:**
- Modify: `parse_excel.py`
- Test: `tests/test_parse_excel.py`

**What changes:**
- `load_excel_file` currently skips rows where tracking is empty (`if fba and trk`). Change to keep rows where `fba` is set, even if `trk` is empty.
- Add "/" filter: rows whose tracking contains "/" are stored in a separate `skipped_slash` list, not in the main tracking entries.
- `group_by_fba_id` stays the same — but callers now get entries with potentially empty `tracking` field.
- Add new function `categorize_shipments(grouped)` that splits into `has_tracking` and `missing_tracking`.

**Step 1: Write failing tests**

Add to `tests/test_parse_excel.py`:

```python
from parse_excel import categorize_shipments, group_by_fba_id

def test_categorize_splits_missing_from_has_tracking():
    grouped = {
        "FBA001": [{"tracking": "1Z123", "carrier": "UPS", "row_number": 2}],
        "FBA002": [{"tracking": "", "carrier": "", "row_number": 3}],
        "FBA003": [
            {"tracking": "1ZABC", "carrier": "UPS", "row_number": 4},
            {"tracking": "", "carrier": "", "row_number": 5},
        ],
    }
    has, missing = categorize_shipments(grouped)
    assert "FBA001" in has
    assert "FBA002" in missing
    assert "FBA003" in has   # has at least one tracking entry

def test_slash_tracking_excluded_from_has_tracking():
    grouped = {
        "FBA004": [{"tracking": "1Z123/456", "carrier": "UPS", "row_number": 2}],
    }
    has, missing = categorize_shipments(grouped)
    assert "FBA004" in missing  # only tracking had "/" — treated as missing

def test_load_excel_file_includes_rows_with_empty_tracking(tmp_path):
    """Rows with FBA ID but no tracking should still appear."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["A", "B", "C", "D_fc", "E_fba", "F", "G", "H_tracking", "I_carrier"])
    ws.append([None, None, None, "BNA1", "FBA999", None, None, "", ""])
    path = tmp_path / "test.xlsx"
    wb.save(path)
    config = {
        "column_fc_code": 3, "column_fba_id": 4,
        "column_tracking": 7, "column_carrier": 8,
    }
    from parse_excel import load_excel_file
    rows = load_excel_file(str(path), config)
    assert any(r["fba_id"] == "FBA999" for r in rows)
```

**Step 2: Run tests to verify they fail**

```
.venv/Scripts/python.exe -m pytest tests/test_parse_excel.py::test_categorize_splits_missing_from_has_tracking tests/test_parse_excel.py::test_slash_tracking_excluded_from_has_tracking tests/test_parse_excel.py::test_load_excel_file_includes_rows_with_empty_tracking -v
```

Expected: FAIL (functions not found / wrong behavior)

**Step 3: Implement changes in parse_excel.py**

In `load_excel_file` (both xlrd and openpyxl branches), change the row inclusion condition:

```python
# OLD (line 99 for xlrd branch, line 115 for openpyxl branch):
if fba and trk:

# NEW — include rows with fba even if trk is empty:
if fba:
```

Add `categorize_shipments` function after `group_by_fba_id`:

```python
def categorize_shipments(grouped: dict) -> tuple[dict, list]:
    """
    Splits grouped FBA shipments into those with usable tracking and those missing it.
    Tracking entries containing "/" are excluded (treated as no tracking).
    Returns: (has_tracking_dict, missing_tracking_list)
      - has_tracking_dict: {"FBA123": [entries with valid tracking only]}
      - missing_tracking_list: ["FBA456", ...] — FBAs with no valid tracking at all
    """
    has_tracking = {}
    missing_tracking = []
    for fba_id, entries in grouped.items():
        valid = [e for e in entries if e.get("tracking") and "/" not in e["tracking"]]
        if valid:
            has_tracking[fba_id] = valid
        else:
            missing_tracking.append(fba_id)
    return has_tracking, missing_tracking
```

**Step 4: Run tests to verify they pass**

```
.venv/Scripts/python.exe -m pytest tests/test_parse_excel.py::test_categorize_splits_missing_from_has_tracking tests/test_parse_excel.py::test_slash_tracking_excluded_from_has_tracking tests/test_parse_excel.py::test_load_excel_file_includes_rows_with_empty_tracking -v
```

Expected: PASS

**Step 5: Run the full test suite to check nothing is broken**

```
.venv/Scripts/python.exe -m pytest tests/ -v
```

Expected: all existing tests still PASS

---

### Task 2: Add shipment record file writing

**Files:**
- Modify: `run.py`

**What changes:**
After parsing + categorizing, write two plain-text files to `logs/`:
- `shipments_missing_tracking_<ts>.txt` — FBA IDs with no usable tracking, one per line
- `shipments_with_tracking_<ts>.txt` — FBA IDs that have tracking, one per line

No tests needed for file writing (it's a simple I/O side-effect). Verify by running the script.

**Step 1: Add `write_shipment_records` function to run.py**

Add this function before `main()` in `run.py`:

```python
def write_shipment_records(has_tracking: dict, missing_tracking: list, logs_folder: str) -> None:
    """Writes two plain-text files: one for FBAs with tracking, one for those missing it."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logs = Path(logs_folder)

    with_file = logs / f"shipments_with_tracking_{ts}.txt"
    with_file.write_text("\n".join(sorted(has_tracking.keys())), encoding="utf-8")

    missing_file = logs / f"shipments_missing_tracking_{ts}.txt"
    missing_file.write_text("\n".join(sorted(missing_tracking)), encoding="utf-8")

    logging.getLogger(__name__).info(
        f"Shipment records written:\n"
        f"  With tracking ({len(has_tracking)}): {with_file}\n"
        f"  Missing tracking ({len(missing_tracking)}): {missing_file}"
    )
    print(f"\nShipment records saved:")
    print(f"  With tracking: {len(has_tracking)} FBAs -> {with_file.name}")
    print(f"  Missing tracking: {len(missing_tracking)} FBAs -> {missing_file.name}")
```

**Step 2: Update the import in run.py**

In the `from parse_excel import parse_and_filter` line, add `categorize_shipments`:

```python
from parse_excel import parse_and_filter, categorize_shipments
```

**Step 3: Update main() to use categorize_shipments and write records**

Replace this block in `main()` (currently around line 162-184):

```python
# OLD:
logger.info("Reading Excel file from input folder...")
shipments_raw = parse_and_filter(config)
...
total_main = sum(len(v) for v in shipments_raw.values())
print(f"\nFound {len(shipments_raw)} US FBA shipments with {total_main} main tracking numbers.")
```

```python
# NEW:
logger.info("Reading Excel file from input folder...")
shipments_all = parse_and_filter(config)   # now includes FBAs with empty tracking too

if args.only_fba:
    if args.only_fba not in shipments_all:
        print(f"\nERROR: FBA ID '{args.only_fba}' not found in Excel.")
        return
    shipments_all = {args.only_fba: shipments_all[args.only_fba]}
    print(f"\nRunning for single shipment: {args.only_fba}")

if not shipments_all:
    print(f"\nNo US FBA shipments found.")
    print(f"  - Drop your Excel file in:  {config['input_folder']}")
    print(f"  - Check column D has US FC codes (e.g. BNA, PHX, IND)")
    print(f"  - Check us_fc_codes.txt has the right prefixes")
    try:
        input("\nPress Enter to exit...")
    except EOFError:
        pass
    return

# Categorize: split into has-tracking vs missing-tracking (also filters out "/" entries)
shipments_raw, missing_tracking = categorize_shipments(shipments_all)
write_shipment_records(shipments_raw, missing_tracking, config["logs_folder"])

if missing_tracking:
    print(f"\n  {len(missing_tracking)} FBA(s) have no usable tracking in Excel — recorded to logs.")

total_main = sum(len(v) for v in shipments_raw.values())
print(f"\nFound {len(shipments_raw)} US FBA shipments with {total_main} trackable entries.")
```

Also remove the duplicate `args.only_fba` block that currently appears before `if not shipments_raw:` — the new code above handles it. The remaining flow below uses `shipments_raw` exactly as before.

**Step 4: Verify manually**

```
.venv/Scripts/python.exe run.py --skip-carrier
```

Check `logs/` folder for two new `.txt` files with correct FBA ID lists.

---

### Task 3: Highlight updated rows in output Excel

**Files:**
- Create: `highlight_excel.py`
- Modify: `run.py`
- Test: `tests/test_highlight_excel.py`

**What changes:**
Instead of just copying/moving the file, open the original Excel, apply a yellow fill to rows where the FBA ID was successfully uploaded, and save to output folder.

Supported for `.xlsx` only (openpyxl write). For `.xls` input, the output is saved as `.xlsx`.

**Step 1: Write failing test**

Create `tests/test_highlight_excel.py`:

```python
import pytest
import openpyxl
from pathlib import Path
from openpyxl.styles import PatternFill

def test_highlight_rows_applies_yellow_fill(tmp_path):
    from highlight_excel import highlight_and_save

    # Create a test workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["A", "B", "C", "D_fc", "E_fba", "F", "G", "H_tracking", "I_carrier"])
    ws.append([None, None, None, "BNA1", "FBA001", None, None, "1Z123", "UPS"])
    ws.append([None, None, None, "BNA1", "FBA002", None, None, "1Z456", "UPS"])
    ws.append([None, None, None, "BNA1", "FBA003", None, None, "1ZABC", "UPS"])
    src = tmp_path / "test.xlsx"
    wb.save(src)

    # Row 2 = FBA001 (updated), row 3 = FBA002 (not updated), row 4 = FBA003 (updated)
    updated_rows = {2, 4}  # 1-indexed Excel rows
    dest = tmp_path / "output.xlsx"

    highlight_and_save(str(src), str(dest), updated_rows)

    result = openpyxl.load_workbook(dest)
    ws2 = result.active
    yellow = "FFFFFF00"
    assert ws2.cell(2, 1).fill.fgColor.rgb == yellow  # FBA001 highlighted
    assert ws2.cell(3, 1).fill.fgColor.rgb != yellow  # FBA002 not highlighted
    assert ws2.cell(4, 1).fill.fgColor.rgb == yellow  # FBA003 highlighted

def test_highlight_saves_to_xlsx_for_xls_input(tmp_path):
    """For .xls input, output should be saved as .xlsx."""
    # We simulate by passing a .xls dest path — function should convert to .xlsx
    from highlight_excel import highlight_and_save
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["header"])
    ws.append(["data"])
    src = tmp_path / "test.xlsx"  # use xlsx as source (xlrd not easy in test)
    wb.save(src)
    dest = tmp_path / "output.xls"  # request .xls output
    result_path = highlight_and_save(str(src), str(dest), {2})
    assert result_path.endswith(".xlsx")
    assert Path(result_path).exists()
```

**Step 2: Run to verify tests fail**

```
.venv/Scripts/python.exe -m pytest tests/test_highlight_excel.py -v
```

Expected: FAIL (module not found)

**Step 3: Create highlight_excel.py**

```python
# highlight_excel.py
from pathlib import Path
import openpyxl
from openpyxl.styles import PatternFill, PatternFillValues


HIGHLIGHT_FILL = PatternFill(fill_type="solid", fgColor="FFFF00")  # yellow


def highlight_and_save(src_path: str, dest_path: str, updated_rows: set) -> str:
    """
    Opens src_path Excel file, applies yellow highlight to all cells in updated_rows
    (1-indexed row numbers), and saves to dest_path.

    For .xls source files, reads via openpyxl after converting; output is always .xlsx.
    Returns the actual path written (may differ if extension changed to .xlsx).

    Args:
        src_path: Path to original Excel file (.xlsx or .xls)
        dest_path: Desired output path
        updated_rows: Set of 1-indexed row numbers to highlight
    """
    src = Path(src_path)
    dest = Path(dest_path)

    # Always output as .xlsx (openpyxl write-only supports xlsx)
    if dest.suffix.lower() != ".xlsx":
        dest = dest.with_suffix(".xlsx")

    if src.suffix.lower() == ".xls":
        # xlrd can't write; read with openpyxl data_only after xlrd load isn't feasible.
        # For .xls, we do a best-effort: convert by re-reading with xlrd and writing xlsx.
        wb = _load_xls_as_workbook(src_path)
    else:
        wb = openpyxl.load_workbook(src_path)

    ws = wb.active
    max_col = ws.max_column or 1

    for row_num in updated_rows:
        for col in range(1, max_col + 1):
            ws.cell(row=row_num, column=col).fill = HIGHLIGHT_FILL

    dest.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(dest))
    return str(dest)


def _load_xls_as_workbook(xls_path: str) -> openpyxl.Workbook:
    """Reads a .xls file via xlrd and returns an openpyxl Workbook copy."""
    import xlrd
    xls_wb = xlrd.open_workbook(xls_path)
    xls_sheet = xls_wb.sheet_by_index(0)
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in range(xls_sheet.nrows):
        row_data = []
        for c in range(xls_sheet.ncols):
            cell = xls_sheet.cell(r, c)
            if cell.ctype == xlrd.XL_CELL_NUMBER:
                val = cell.value
                row_data.append(int(val) if val == int(val) else val)
            else:
                row_data.append(cell.value)
        ws.append(row_data)
    return wb
```

**Step 4: Run tests to verify they pass**

```
.venv/Scripts/python.exe -m pytest tests/test_highlight_excel.py -v
```

Expected: PASS

---

### Task 4: Wire highlighting into run.py (replace move_processed_files)

**Files:**
- Modify: `run.py`

**What changes:**
After a successful upload, instead of calling `move_processed_files`, call `highlight_and_save` on each input file using the row numbers of successfully uploaded shipments, then save to output folder.

**Step 1: Collect updated row numbers during parse**

The `parse_excel.py` `load_excel_file` already stores `row_number` in each entry dict. After upload, we know which FBA IDs succeeded. Cross-reference to get row numbers.

Add this helper to `run.py`:

```python
def collect_updated_row_numbers(shipments_all: dict, results: list) -> set:
    """
    Returns set of 1-indexed Excel row numbers for FBA IDs that were successfully uploaded.
    shipments_all: full grouped dict from parse_and_filter (includes row_number per entry)
    results: list of upload result dicts with 'fba_id' and 'status'
    """
    successful_fba_ids = {
        r["fba_id"] for r in results if r["status"] in ("success", "partial")
    }
    rows = set()
    for fba_id in successful_fba_ids:
        for entry in shipments_all.get(fba_id, []):
            rn = entry.get("row_number")
            if rn:
                rows.add(rn)
    return rows
```

**Step 2: Replace move_processed_files call with highlight_and_save**

In `run.py`, add import at top of the deferred import block:

```python
from highlight_excel import highlight_and_save
```

Replace the `move_processed_files(config)` call (around line 256) with:

```python
# Highlight updated rows and save to output folder
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
input_folder = Path(config["input_folder"])
output_folder = Path(config["output_folder"])
updated_rows = collect_updated_row_numbers(shipments_all, results)

for pattern in ["*.xls", "*.xlsx"]:
    for src_file in input_folder.glob(pattern):
        dest_file = output_folder / f"{src_file.stem}_processed_{ts}.xlsx"
        actual_dest = highlight_and_save(str(src_file), str(dest_file), updated_rows)
        src_file.unlink()  # remove from input after saving highlighted copy
        logger.info(f"Highlighted output saved: {actual_dest}")
        print(f"Output saved with highlights: {Path(actual_dest).name}")
```

Note: `shipments_all` must be in scope here — make sure it's declared before the `try` block (same as how `results = []` is currently declared before the try).

**Step 3: Verify end-to-end manually**

```
.venv/Scripts/python.exe run.py --skip-carrier
```

Check that:
1. `logs/` has two `shipments_*_tracking_*.txt` files with FBA IDs only
2. `output/` has the processed Excel file
3. Open the Excel — rows where tracking was uploaded should be yellow-highlighted
4. Rows where FBA had "/" tracking or no tracking should not be highlighted

**Step 4: Run full test suite**

```
.venv/Scripts/python.exe -m pytest tests/ -v
```

Expected: all PASS

---

## Summary of All Changed Files

| File | Change |
|---|---|
| `parse_excel.py` | Load FBA rows with empty tracking; add `categorize_shipments()`; "/" filter |
| `highlight_excel.py` | NEW — `highlight_and_save()` applies yellow fill to updated rows |
| `run.py` | Use `categorize_shipments`, call `write_shipment_records`, replace file-move with highlighted save |
| `tests/test_parse_excel.py` | 3 new tests for categorize + "/" filter + empty-tracking loading |
| `tests/test_highlight_excel.py` | NEW — 2 tests for highlighting logic |
