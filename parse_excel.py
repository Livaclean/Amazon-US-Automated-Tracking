# parse_excel.py
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def detect_excel_engine(file_path: str) -> str:
    """Returns 'xlrd' for .xls, 'openpyxl' for .xlsx. Raises ValueError for other extensions."""
    suffix = Path(file_path).suffix.lower()
    if suffix == ".xls":
        return "xlrd"
    if suffix == ".xlsx":
        return "openpyxl"
    raise ValueError(f"Unsupported file extension: {suffix!r}. Expected .xls or .xlsx.")


def load_us_fc_prefixes(us_fc_codes_file: str) -> set:
    """Reads us_fc_codes.txt, returns set of uppercase 3-letter prefixes."""
    prefixes = set()
    try:
        with open(us_fc_codes_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    prefixes.add(line.upper())
    except FileNotFoundError:
        logger.warning(f"US FC codes file not found: {us_fc_codes_file}")
    return prefixes


def is_us_fc(fc_code, us_prefixes: set) -> bool:
    """True if fc_code starts with a known US 3-letter FC prefix."""
    if not fc_code:
        return False
    fc_str = str(fc_code).strip().upper()
    return len(fc_str) >= 3 and fc_str[:3] in us_prefixes


def group_by_fba_id(rows: list) -> dict:
    """
    Groups rows by FBA ID. Deduplicates tracking entries.
    Returns: {"FBA123": [{"tracking": "...", "carrier": "..."}, ...]}
    Skips rows with empty/None fba_id.
    """
    result = {}
    for row in rows:
        fba_id = str(row.get("fba_id") or "").strip()
        if not fba_id:
            continue
        entry = {
            "tracking": str(row.get("tracking_num", "")).strip(),
            "carrier": str(row.get("carrier", "")).strip(),
            "row_number": row.get("row_number"),
        }
        if fba_id not in result:
            result[fba_id] = []
        if entry not in result[fba_id]:
            result[fba_id].append(entry)
    return result


def categorize_shipments(grouped: dict) -> tuple:
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


def _xlrd_cell_str(sheet, row, col) -> str:
    """
    Converts an xlrd cell value to a clean string.
    Numeric cells (e.g. tracking numbers stored as floats) are converted to
    integers first so that 1234567890.0 becomes "1234567890" not "1234567890.0".
    """
    import xlrd
    cell = sheet.cell(row, col)
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        val = cell.value
        # If the float is a whole number, return as integer string.
        return str(int(val)) if val == int(val) else str(val)
    return str(cell.value).strip()


def load_excel_file(file_path: str, config: dict) -> list:
    """
    Loads Excel file rows as dicts using column indices from config.
    Default: D=3 (fc_code), E=4 (fba_id), H=7 (tracking_num), I=8 (carrier).
    Skips header row and rows missing fba_id. Rows with empty tracking_num are included.
    """
    col_fc = config.get("column_fc_code", 3)
    col_fba = config.get("column_fba_id", 4)
    col_tracking = config.get("column_tracking", 7)
    col_carrier = config.get("column_carrier", 8)
    rows = []

    if detect_excel_engine(file_path) == "xlrd":
        import xlrd
        wb = xlrd.open_workbook(file_path)
        sheet = wb.sheet_by_index(0)
        for r in range(1, sheet.nrows):
            try:
                fc = _xlrd_cell_str(sheet, r, col_fc).strip()
                fba = _xlrd_cell_str(sheet, r, col_fba).strip()
                trk = _xlrd_cell_str(sheet, r, col_tracking).strip()
                car = _xlrd_cell_str(sheet, r, col_carrier).strip() if sheet.ncols > col_carrier else ""
                if fba:
                    rows.append({"fc_code": fc, "fba_id": fba, "tracking_num": trk,
                                 "carrier": car, "row_number": r + 1})
            except IndexError:
                logger.warning(f"Row {r+1}: IndexError — check column indices in config.json (column_fc_code={col_fc}, column_fba_id={col_fba}, column_tracking={col_tracking}, column_carrier={col_carrier})")
                break  # Stop processing if config is wrong — don't silently skip all rows
    else:
        from openpyxl import load_workbook
        wb = load_workbook(file_path, read_only=True, data_only=True)
        sheet = wb.active
        for idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True)):
            try:
                fc = str(row[col_fc] or "").strip()
                fba = str(row[col_fba] or "").strip()
                trk = str(row[col_tracking] or "").strip()
                car = str(row[col_carrier] or "").strip() if len(row) > col_carrier else ""
                if fba:
                    rows.append({"fc_code": fc, "fba_id": fba, "tracking_num": trk,
                                 "carrier": car, "row_number": idx + 2})
            except (IndexError, TypeError):
                logger.warning(f"Row {idx+2}: IndexError/TypeError — check column indices in config.json")
                break
    return rows


def find_excel_files(input_folder: str) -> list:
    """Returns sorted list of .xls/.xlsx files in input_folder."""
    folder = Path(input_folder)
    if not folder.exists():
        return []
    files = sorted(
        f for pattern in ["*.xls", "*.xlsx"] for f in folder.glob(pattern)
    )
    return [str(f) for f in files]


def parse_and_filter(config: dict) -> dict:
    """
    Top-level: finds Excel files, loads rows, filters US FCs, groups by FBA ID.
    Returns: {"FBA123": [{"tracking": "...", "carrier": "..."}, ...]}
    """
    excel_files = find_excel_files(config["input_folder"])
    if not excel_files:
        logger.warning(f"No Excel files found in {config['input_folder']}")
        return {}
    if len(excel_files) > 1:
        logger.warning(f"Multiple Excel files found — processing all: {excel_files}")

    us_prefixes = load_us_fc_prefixes(config.get("us_fc_codes_file", "us_fc_codes.txt"))
    if not us_prefixes:
        logger.warning("No US FC prefixes loaded — check us_fc_codes.txt")

    all_us_rows = []
    for file_path in excel_files:
        logger.info(f"Reading: {file_path}")
        all_rows = load_excel_file(file_path, config)
        us_rows = [r for r in all_rows if is_us_fc(r["fc_code"], us_prefixes)]
        logger.info(f"  {len(us_rows)} US rows (skipped {len(all_rows) - len(us_rows)} non-US)")
        all_us_rows.extend(us_rows)

    return group_by_fba_id(all_us_rows)
