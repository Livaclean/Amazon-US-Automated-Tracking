# Amazon FBA Tracking Upload Automation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automate uploading carrier sub-package tracking IDs to Amazon Seller Central by reading a supplier Excel file, scraping UPS/FedEx tracking pages for individual package numbers, then uploading them to each FBA shipment page.

**Architecture:** A Python script reads an Excel file (column D = US FC filter, E = FBA shipment ID, H = main tracking number, I = carrier), filters US-only shipments, uses Playwright to open UPS or FedEx tracking pages and scrape individual sub-package tracking IDs, then navigates to each Amazon FBA shipment page and uploads all sub-IDs using the same Chrome browser session. The user double-clicks `run.bat` each time; Chrome reuses their existing logged-in session.

**Tech Stack:** Python 3.x, Playwright (browser automation + Chrome profile reuse), openpyxl + xlrd (Excel reading), pytest (tests), Windows batch files (user runner).

---

## Project Layout

All files in: `C:\Users\hadis\OneDrive\Desktop\Automated Tracking Tracking\`
(abbreviated as `ROOT\` below)

```
ROOT\
├── docs\plans\               ← this file
├── input\                    ← user drops Excel file here
├── output\                   ← processed files auto-moved here
├── logs\
│   └── screenshots\          ← error screenshots
├── tests\
│   ├── test_parse_excel.py
│   └── test_fetch_sub_tracking.py
├── config.json
├── us_fc_codes.txt
├── requirements.txt
├── parse_excel.py
├── fetch_sub_tracking.py
├── upload_tracking.py
├── run.py
├── run.bat
└── setup.bat
```

---

### Task 1: Project scaffold and config

**Files:**
- Create: `ROOT\requirements.txt`
- Create: `ROOT\config.json`
- Create: `ROOT\us_fc_codes.txt`

**Step 1: Create `requirements.txt`**

```
playwright>=1.40.0
openpyxl>=3.1.0
xlrd>=2.0.1
pytest>=7.0.0
```

**Step 2: Create `config.json`**

```json
{
  "input_folder": "C:\\Users\\hadis\\OneDrive\\Desktop\\Automated Tracking Tracking\\input",
  "output_folder": "C:\\Users\\hadis\\OneDrive\\Desktop\\Automated Tracking Tracking\\output",
  "logs_folder": "C:\\Users\\hadis\\OneDrive\\Desktop\\Automated Tracking Tracking\\logs",
  "chrome_profile_path": "C:\\Users\\hadis\\AppData\\Local\\Google\\Chrome\\User Data",
  "chrome_profile_name": "Default",
  "amazon_base_url": "https://sellercentral.amazon.com",
  "headless": false,
  "delay_between_shipments_seconds": 2,
  "delay_between_tracking_numbers_seconds": 1,
  "column_fc_code": 3,
  "column_fba_id": 4,
  "column_tracking": 7,
  "column_carrier": 8,
  "us_fc_codes_file": "us_fc_codes.txt"
}
```

**Step 3: Create `us_fc_codes.txt`**

```
# US Amazon Fulfillment Center 3-letter prefixes
# One prefix per line. Lines starting with # are comments.
# Add new prefixes here as Amazon opens new US FCs.
ABE
ABQ
AGS
ALB
ATL
AVP
AZA
BDL
BFI
BHM
BNA
BOI
BOS
BUF
BWI
CAE
CAK
CHA
CHS
CLE
CLT
CMH
CQS
DAL
DEN
DET
DFW
DSM
DTW
ELP
EWR
FAT
FLL
FTW
GEG
GYR
HOU
IAD
ICT
IND
JAX
JFK
LAS
LAX
LGB
LIT
MCI
MCO
MDT
MEM
MIA
MKE
MQY
MSP
MSY
OAK
OKC
OMA
ONT
ORD
ORF
PDX
PHL
PHX
PIT
RDU
RFD
RIC
RNO
RSW
SAT
SBD
SCK
SEA
SFO
SJC
SLC
SMF
SNA
STL
TUL
TUS
TYS
```

**Step 4: Create input/output/logs folders**

```bash
mkdir -p "C:\Users\hadis\OneDrive\Desktop\Automated Tracking Tracking\input"
mkdir -p "C:\Users\hadis\OneDrive\Desktop\Automated Tracking Tracking\output"
mkdir -p "C:\Users\hadis\OneDrive\Desktop\Automated Tracking Tracking\logs"
mkdir -p "C:\Users\hadis\OneDrive\Desktop\Automated Tracking Tracking\logs\screenshots"
mkdir -p "C:\Users\hadis\OneDrive\Desktop\Automated Tracking Tracking\tests"
```

**Step 5: Commit**

```bash
git init
git add requirements.txt config.json us_fc_codes.txt
git commit -m "feat: add project scaffold and config"
```

---

### Task 2: `parse_excel.py` — Excel reading and US FC filtering

**Files:**
- Create: `ROOT\tests\test_parse_excel.py`
- Create: `ROOT\parse_excel.py`

**Step 1: Write failing tests**

Create `ROOT\tests\test_parse_excel.py`:

```python
import pytest
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from parse_excel import (
    detect_excel_engine,
    load_us_fc_prefixes,
    is_us_fc,
    group_by_fba_id,
)


def test_detect_engine_xls():
    assert detect_excel_engine("shipments.xls") == "xlrd"

def test_detect_engine_xlsx():
    assert detect_excel_engine("shipments.xlsx") == "openpyxl"

def test_detect_engine_case_insensitive():
    assert detect_excel_engine("SHIPMENTS.XLS") == "xlrd"
    assert detect_excel_engine("SHIPMENTS.XLSX") == "openpyxl"


def test_load_us_fc_prefixes(tmp_path):
    fc_file = tmp_path / "us_fc_codes.txt"
    fc_file.write_text("# comment\nBNA\nPHX\n\nRIC\n")
    result = load_us_fc_prefixes(str(fc_file))
    assert result == {"BNA", "PHX", "RIC"}

def test_load_us_fc_prefixes_empty(tmp_path):
    fc_file = tmp_path / "us_fc_codes.txt"
    fc_file.write_text("# only comments\n\n")
    assert load_us_fc_prefixes(str(fc_file)) == set()


def test_is_us_fc_match():
    assert is_us_fc("BNA6", {"BNA", "PHX"}) is True

def test_is_us_fc_no_match():
    assert is_us_fc("YYZ1", {"BNA", "PHX"}) is False

def test_is_us_fc_case_insensitive():
    assert is_us_fc("bna6", {"BNA"}) is True

def test_is_us_fc_too_short():
    assert is_us_fc("BN", {"BNA"}) is False

def test_is_us_fc_none():
    assert is_us_fc(None, {"BNA"}) is False

def test_is_us_fc_strips_whitespace():
    assert is_us_fc("  BNA6  ", {"BNA"}) is True


def test_group_by_fba_id_basic():
    rows = [
        {"fba_id": "FBA123", "tracking_num": "1Z001", "carrier": "UPS"},
        {"fba_id": "FBA123", "tracking_num": "1Z002", "carrier": "UPS"},
        {"fba_id": "FBA456", "tracking_num": "999001", "carrier": "FedEx"},
    ]
    result = group_by_fba_id(rows)
    assert result["FBA123"] == [
        {"tracking": "1Z001", "carrier": "UPS"},
        {"tracking": "1Z002", "carrier": "UPS"},
    ]
    assert result["FBA456"] == [{"tracking": "999001", "carrier": "FedEx"}]

def test_group_by_fba_id_deduplication():
    rows = [
        {"fba_id": "FBA123", "tracking_num": "1Z001", "carrier": "UPS"},
        {"fba_id": "FBA123", "tracking_num": "1Z001", "carrier": "UPS"},
    ]
    result = group_by_fba_id(rows)
    assert len(result["FBA123"]) == 1

def test_group_by_fba_id_skips_empty_fba():
    rows = [
        {"fba_id": "", "tracking_num": "1Z001", "carrier": "UPS"},
        {"fba_id": None, "tracking_num": "1Z002", "carrier": "UPS"},
        {"fba_id": "FBA123", "tracking_num": "1Z003", "carrier": "UPS"},
    ]
    result = group_by_fba_id(rows)
    assert list(result.keys()) == ["FBA123"]
```

**Step 2: Run tests to confirm failure**

```bash
cd "C:\Users\hadis\OneDrive\Desktop\Automated Tracking Tracking"
pip install pytest
pytest tests/test_parse_excel.py -v
```

Expected: `ImportError: No module named 'parse_excel'`

**Step 3: Create `ROOT\parse_excel.py`**

```python
# parse_excel.py
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def detect_excel_engine(file_path: str) -> str:
    """Returns 'xlrd' for .xls, 'openpyxl' for .xlsx."""
    return "xlrd" if Path(file_path).suffix.lower() == ".xls" else "openpyxl"


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
        }
        if fba_id not in result:
            result[fba_id] = []
        if entry not in result[fba_id]:
            result[fba_id].append(entry)
    return result


def load_excel_file(file_path: str, config: dict) -> list:
    """
    Loads Excel file rows as dicts using column indices from config.
    Default: D=3 (fc_code), E=4 (fba_id), H=7 (tracking_num), I=8 (carrier).
    Skips header row and rows missing fba_id or tracking_num.
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
                fc = str(sheet.cell_value(r, col_fc)).strip()
                fba = str(sheet.cell_value(r, col_fba)).strip()
                trk = str(sheet.cell_value(r, col_tracking)).strip()
                car = str(sheet.cell_value(r, col_carrier)).strip() if sheet.ncols > col_carrier else ""
                if fba and trk:
                    rows.append({"fc_code": fc, "fba_id": fba, "tracking_num": trk,
                                 "carrier": car, "row_number": r + 1})
            except IndexError:
                pass
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
                if fba and trk and fba != "None" and trk != "None":
                    rows.append({"fc_code": fc, "fba_id": fba, "tracking_num": trk,
                                 "carrier": car, "row_number": idx + 2})
            except (IndexError, TypeError):
                pass
    return rows


def find_excel_files(input_folder: str) -> list:
    """Returns sorted list of .xls/.xlsx files in input_folder."""
    folder = Path(input_folder)
    if not folder.exists():
        return []
    files = []
    for pattern in ["*.xls", "*.xlsx"]:
        files.extend(sorted(folder.glob(pattern)))
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
```

**Step 4: Run tests — expect all pass**

```bash
pytest tests/test_parse_excel.py -v
```

Expected: All 13 tests PASS

**Step 5: Commit**

```bash
git add parse_excel.py tests/test_parse_excel.py
git commit -m "feat: add Excel parsing and US FC filtering"
```

---

### Task 3: `fetch_sub_tracking.py` — Scrape UPS/FedEx for sub-package IDs

**Files:**
- Create: `ROOT\tests\test_fetch_sub_tracking.py`
- Create: `ROOT\fetch_sub_tracking.py`

**Step 1: Write failing tests**

Create `ROOT\tests\test_fetch_sub_tracking.py`:

```python
import pytest
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fetch_sub_tracking import (
    normalize_carrier,
    extract_ups_tracking_from_text,
    extract_fedex_tracking_from_text,
    deduplicate_tracking_numbers,
)


def test_normalize_ups_variations():
    for name in ["UPS", "ups", "Ups", "United Parcel Service"]:
        assert normalize_carrier(name) == "ups"

def test_normalize_fedex_variations():
    for name in ["FedEx", "fedex", "FEDEX", "Federal Express"]:
        assert normalize_carrier(name) == "fedex"

def test_normalize_unknown():
    assert normalize_carrier("DHL") == "unknown"
    assert normalize_carrier("") == "unknown"
    assert normalize_carrier(None) == "unknown"


def test_extract_ups_basic():
    text = "Package 1: 1Z999AA10123456784  Package 2: 1Z999AA10123456785"
    result = extract_ups_tracking_from_text(text)
    assert "1Z999AA10123456784" in result
    assert "1Z999AA10123456785" in result

def test_extract_ups_excludes_master():
    master = "1ZMASTER0000000001"
    text = f"Shipment: {master}  Package: 1Z999AA10123456784"
    result = extract_ups_tracking_from_text(text, exclude=master)
    assert master.upper() not in result
    assert "1Z999AA10123456784" in result

def test_extract_ups_empty():
    assert extract_ups_tracking_from_text("") == []

def test_extract_ups_no_matches():
    assert extract_ups_tracking_from_text("no tracking numbers here") == []


def test_extract_fedex_12digit():
    text = "Tracking: 123456789012  Also: 987654321098"
    result = extract_fedex_tracking_from_text(text)
    assert "123456789012" in result
    assert "987654321098" in result

def test_extract_fedex_20digit():
    text = "Tracking: 12345678901234567890"
    result = extract_fedex_tracking_from_text(text)
    assert "12345678901234567890" in result

def test_extract_fedex_excludes_short():
    # 10-digit numbers should NOT match (min is 12)
    text = "Call 1234567890 for support"
    result = extract_fedex_tracking_from_text(text)
    assert "1234567890" not in result


def test_deduplicate_basic():
    result = deduplicate_tracking_numbers(["1Z001", "1Z002", "1Z001"])
    assert result == ["1Z001", "1Z002"]

def test_deduplicate_preserves_order():
    result = deduplicate_tracking_numbers(["1Z003", "1Z001", "1Z002"])
    assert result == ["1Z003", "1Z001", "1Z002"]
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_fetch_sub_tracking.py -v
```

Expected: `ImportError: No module named 'fetch_sub_tracking'`

**Step 3: Create `ROOT\fetch_sub_tracking.py`**

```python
# fetch_sub_tracking.py
import re
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# UPS: 1Z + 16 alphanumeric chars
UPS_PATTERN = re.compile(r"\b(1Z[0-9A-Z]{16})\b", re.IGNORECASE)
# FedEx: 12–22 digits
FEDEX_PATTERN = re.compile(r"\b(\d{12,22})\b")

UPS_TRACK_URL = "https://www.ups.com/track?tracknum={tracking}&loc=en_US"
FEDEX_TRACK_URL = "https://www.fedex.com/fedextrack/?trknbr={tracking}"

UPS_SELECTORS = [
    "[data-testid*='package-tracking-number']",
    "[data-testid*='trackingNumber']",
    ".pkg-info__tracking-number",
    ".package-tracking-number",
    "[class*='trackingNum']",
]

FEDEX_SELECTORS = [
    "[data-testid*='tracking-number']",
    ".tracking-number-value",
    "[class*='trackingNum']",
    ".shipment-piece-tracking",
    "[aria-label*='tracking number']",
]


def normalize_carrier(carrier_name) -> str:
    """Returns 'ups', 'fedex', or 'unknown'."""
    if not carrier_name:
        return "unknown"
    name = str(carrier_name).lower().strip()
    if "ups" in name or "united parcel" in name:
        return "ups"
    if "fedex" in name or "federal express" in name:
        return "fedex"
    return "unknown"


def extract_ups_tracking_from_text(text: str, exclude: str = None) -> list:
    """Extracts all 1Z-format UPS tracking numbers from text."""
    matches = UPS_PATTERN.findall(text.upper())
    result = []
    for m in matches:
        if exclude and m.upper() == str(exclude).upper():
            continue
        result.append(m.upper())
    return result


def extract_fedex_tracking_from_text(text: str, exclude: str = None) -> list:
    """Extracts all 12–22 digit FedEx tracking numbers from text."""
    result = []
    for m in FEDEX_PATTERN.findall(text):
        if exclude and m == str(exclude):
            continue
        result.append(m)
    return result


def deduplicate_tracking_numbers(numbers: list) -> list:
    """Removes duplicates while preserving order."""
    seen = set()
    result = []
    for n in numbers:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _try_selectors(page, selectors: list) -> list:
    """Tries each CSS selector, returns text content of all matching elements."""
    for selector in selectors:
        try:
            elements = page.query_selector_all(selector)
            texts = [el.text_content().strip() for el in elements if el.text_content().strip()]
            if texts:
                logger.debug(f"Selector '{selector}' matched {len(texts)} elements")
                return texts
        except Exception:
            continue
    return []


def _handle_captcha(page) -> None:
    """Pauses if a CAPTCHA is detected and waits for user to solve it."""
    if "captcha" in page.url.lower() or page.query_selector("iframe[title*='challenge']"):
        logger.warning("CAPTCHA detected — please solve it manually in the browser")
        print("\n  ACTION REQUIRED: Solve the CAPTCHA in the browser window, then press Enter.")
        input()
        page.wait_for_load_state("networkidle", timeout=20000)


def fetch_ups_sub_tracking(page, main_tracking: str, logs_folder: str = None) -> list:
    """Opens UPS tracking page, returns list of sub-package tracking IDs."""
    url = UPS_TRACK_URL.format(tracking=main_tracking)
    logger.info(f"  UPS tracking: {url}")
    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)
        time.sleep(2)
    except Exception as e:
        logger.error(f"  Failed to load UPS page: {e}")
        return []

    _handle_captcha(page)

    # Try CSS selectors first
    texts = _try_selectors(page, UPS_SELECTORS)
    if texts:
        numbers = []
        for t in texts:
            numbers.extend(extract_ups_tracking_from_text(t, exclude=main_tracking))
        if numbers:
            logger.info(f"  Found {len(numbers)} UPS sub-IDs via selectors")
            return deduplicate_tracking_numbers(numbers)

    # Fallback: regex on full page text
    try:
        page_text = page.inner_text("body")
        numbers = deduplicate_tracking_numbers(
            extract_ups_tracking_from_text(page_text, exclude=main_tracking)
        )
        logger.info(f"  Found {len(numbers)} UPS sub-IDs via regex fallback")
        if logs_folder:
            Path(logs_folder).joinpath(f"ups_page_{main_tracking}.txt").write_text(page_text[:5000])
        return numbers
    except Exception as e:
        logger.error(f"  UPS page text extraction failed: {e}")
        return []


def fetch_fedex_sub_tracking(page, main_tracking: str, logs_folder: str = None) -> list:
    """Opens FedEx tracking page, returns list of sub-package tracking IDs."""
    url = FEDEX_TRACK_URL.format(tracking=main_tracking)
    logger.info(f"  FedEx tracking: {url}")
    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)
        time.sleep(3)
    except Exception as e:
        logger.error(f"  Failed to load FedEx page: {e}")
        return []

    _handle_captcha(page)

    texts = _try_selectors(page, FEDEX_SELECTORS)
    if texts:
        numbers = []
        for t in texts:
            numbers.extend(extract_fedex_tracking_from_text(t, exclude=main_tracking))
        if numbers:
            logger.info(f"  Found {len(numbers)} FedEx sub-IDs via selectors")
            return deduplicate_tracking_numbers(numbers)

    try:
        page_text = page.inner_text("body")
        numbers = deduplicate_tracking_numbers(
            extract_fedex_tracking_from_text(page_text, exclude=main_tracking)
        )
        logger.info(f"  Found {len(numbers)} FedEx sub-IDs via regex fallback")
        if logs_folder:
            Path(logs_folder).joinpath(f"fedex_page_{main_tracking}.txt").write_text(page_text[:5000])
        return numbers
    except Exception as e:
        logger.error(f"  FedEx page text extraction failed: {e}")
        return []


def fetch_sub_tracking_ids(page, main_tracking: str, carrier: str,
                            logs_folder: str = None) -> list:
    """Dispatches to UPS or FedEx scraper based on carrier string."""
    normalized = normalize_carrier(carrier)
    if normalized == "ups":
        return fetch_ups_sub_tracking(page, main_tracking, logs_folder)
    elif normalized == "fedex":
        return fetch_fedex_sub_tracking(page, main_tracking, logs_folder)
    else:
        logger.warning(f"  Unknown carrier '{carrier}' for tracking {main_tracking} — skipping")
        return []


def get_all_sub_tracking(page, tracking_entries: list, logs_folder: str = None) -> list:
    """
    For a list of {"tracking": "...", "carrier": "..."} dicts,
    fetches and returns a flat deduplicated list of all sub-tracking IDs.
    """
    all_ids = []
    for entry in tracking_entries:
        tracking = entry.get("tracking", "")
        carrier = entry.get("carrier", "")
        if not tracking:
            continue
        logger.info(f"  Fetching sub-tracking for {tracking} ({carrier})")
        sub_ids = fetch_sub_tracking_ids(page, tracking, carrier, logs_folder)
        if not sub_ids:
            logger.warning(f"  No sub-IDs found for {tracking} — check carrier website manually")
        all_ids.extend(sub_ids)
    return deduplicate_tracking_numbers(all_ids)
```

**Step 4: Run tests — expect all pass**

```bash
pytest tests/test_fetch_sub_tracking.py -v
```

Expected: All 13 tests PASS

**Step 5: Run all tests to confirm nothing broke**

```bash
pytest tests/ -v
```

Expected: All 26 tests PASS

**Step 6: Commit**

```bash
git add fetch_sub_tracking.py tests/test_fetch_sub_tracking.py
git commit -m "feat: add UPS/FedEx sub-tracking scraper"
```

---

### Task 4: `upload_tracking.py` — Amazon Seller Central automation

**Files:**
- Create: `ROOT\upload_tracking.py`

No unit tests — requires live browser and Amazon login. Verified manually in Task 7.

**Step 1: Create `ROOT\upload_tracking.py`**

```python
# upload_tracking.py
import logging
import time
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Layered fallback selectors — try in order.
# Run `python run.py --discover` on first use to see real Amazon selectors,
# then update these lists if needed.
SELECTORS = {
    "add_tracking_button": [
        "button:has-text('Add tracking')",
        "button:has-text('Add Tracking')",
        "button:has-text('Enter tracking')",
        "button:has-text('Enter Tracking')",
        "[data-testid*='add-tracking']",
        "[data-testid*='addTracking']",
        "button[aria-label*='tracking' i]",
        "a:has-text('Add tracking')",
        "a:has-text('Enter tracking')",
    ],
    "tracking_input": [
        "input[placeholder*='tracking' i]",
        "input[name*='tracking' i]",
        "input[data-testid*='tracking']",
        "input[aria-label*='tracking' i]",
        "input[type='text']:visible",
    ],
    "save_button": [
        "button:has-text('Save')",
        "button:has-text('Confirm')",
        "button:has-text('Submit')",
        "[data-testid*='save']",
        "[data-testid*='confirm']",
        "button[type='submit']",
    ],
}

LOGIN_SELECTORS = ["input#ap_email", "input[name='email']", "form[name='signIn']"]
ALREADY_EXISTS_TEXTS = ["already exists", "already been added", "duplicate"]
NOT_FOUND_TEXTS = ["not found", "does not exist", "invalid shipment"]


def _screenshot(page, step_name: str, logs_folder: str) -> None:
    try:
        folder = Path(logs_folder) / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        page.screenshot(path=str(folder / f"{ts}_{step_name}.png"))
    except Exception:
        pass


def _try_click(page, selectors: list, timeout: int = 5000) -> Optional[str]:
    """Clicks first matching selector. Returns matched selector or None."""
    for s in selectors:
        try:
            el = page.wait_for_selector(s, timeout=timeout, state="visible")
            if el:
                el.click()
                return s
        except Exception:
            continue
    return None


def _try_fill(page, selectors: list, value: str, timeout: int = 5000) -> Optional[str]:
    """Fills first matching input. Returns matched selector or None."""
    for s in selectors:
        try:
            el = page.wait_for_selector(s, timeout=timeout, state="visible")
            if el:
                el.click()
                el.fill(value)
                return s
        except Exception:
            continue
    return None


def _page_contains(page, texts: list) -> Optional[str]:
    """Returns first text found in page content (case-insensitive), or None."""
    try:
        content = page.content().lower()
        for t in texts:
            if t.lower() in content:
                return t
    except Exception:
        pass
    return None


def create_browser_context(config: dict):
    """
    Launches Chrome using user's existing profile (preserves Amazon login).
    Chrome must be fully closed before calling this.
    Returns (playwright_instance, context).
    """
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=config["chrome_profile_path"],
            channel="chrome",
            headless=config.get("headless", False),
            args=[
                f"--profile-directory={config.get('chrome_profile_name', 'Default')}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            viewport={"width": 1280, "height": 900},
            slow_mo=500,
        )
        return playwright, context
    except Exception as e:
        playwright.stop()
        if "already in use" in str(e).lower() or "user data directory" in str(e).lower():
            raise RuntimeError(
                "Chrome profile is already in use.\n"
                "Please close Google Chrome completely and try again."
            ) from e
        raise


def check_login_status(page, base_url: str) -> None:
    """
    Goes to Amazon Seller Central. If login page detected, pauses for manual login.
    """
    try:
        page.goto(base_url, timeout=20000)
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    for s in LOGIN_SELECTORS:
        try:
            if page.query_selector(s):
                logger.warning("Amazon login required")
                print("\n" + "=" * 60)
                print("ACTION REQUIRED: Log in to Amazon Seller Central in the")
                print("browser window, then press Enter here to continue.")
                print("=" * 60)
                input()
                page.wait_for_load_state("networkidle", timeout=30000)
                return
        except Exception:
            continue
    logger.info("Amazon Seller Central: already logged in")


def navigate_to_shipment(page, fba_id: str, base_url: str) -> bool:
    """Navigates to /fba/inbound-shipment/summary/{fba_id}/shipmentEvents. Returns True on success."""
    url = f"{base_url}/fba/inbound-shipment/summary/{fba_id}/shipmentEvents"
    logger.info(f"  → {url}")
    try:
        page.goto(url, timeout=20000)
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception as e:
        logger.error(f"  Navigation failed: {e}")
        return False

    # Re-check login (session may expire mid-run)
    for s in LOGIN_SELECTORS:
        try:
            if page.query_selector(s):
                logger.warning("  Session expired — please log in again")
                check_login_status(page, base_url)
                page.goto(url, timeout=20000)
                page.wait_for_load_state("networkidle", timeout=15000)
                break
        except Exception:
            continue

    if _page_contains(page, NOT_FOUND_TEXTS):
        logger.warning(f"  Shipment {fba_id} not found on Amazon")
        return False
    return True


def add_single_tracking_number(page, tracking_number: str, fba_id: str, config: dict) -> dict:
    """
    Adds one tracking number to the current shipment page.
    Returns: {"tracking_number": str, "status": "success"|"already_exists"|"error", "message": str}
    """
    logs_folder = config.get("logs_folder", "logs")
    delay = config.get("delay_between_tracking_numbers_seconds", 1)
    result = {"tracking_number": tracking_number, "status": "error", "message": ""}

    # Click Add tracking button
    clicked = _try_click(page, SELECTORS["add_tracking_button"])
    if not clicked:
        logger.warning(f"  Could not find 'Add tracking' button — please click it manually")
        print(f"\n  ACTION REQUIRED: Click the 'Add tracking' button in the browser, then press Enter.")
        input()

    time.sleep(0.5)

    # Fill tracking number
    if not _try_fill(page, SELECTORS["tracking_input"], tracking_number):
        logger.error(f"  Could not find tracking input")
        _screenshot(page, f"no_input_{tracking_number[:10]}", logs_folder)
        result["message"] = "Could not find tracking input field"
        return result

    time.sleep(0.3)

    # Click Save
    if not _try_click(page, SELECTORS["save_button"]):
        logger.error(f"  Could not find Save button")
        _screenshot(page, f"no_save_{tracking_number[:10]}", logs_folder)
        result["message"] = "Could not find Save button"
        return result

    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    time.sleep(delay)

    if _page_contains(page, ALREADY_EXISTS_TEXTS):
        logger.info(f"  {tracking_number}: already exists (skipped)")
        result["status"] = "already_exists"
        result["message"] = "Already on this shipment"
        return result

    if _page_contains(page, ["error", "invalid", "failed"]):
        _screenshot(page, f"error_{tracking_number[:10]}", logs_folder)
        result["status"] = "error"
        result["message"] = "Page showed an error after saving"
        return result

    logger.info(f"  {tracking_number}: added successfully")
    result["status"] = "success"
    result["message"] = "Added"
    return result


def discover_page_elements(page, fba_id: str, base_url: str, logs_folder: str) -> None:
    """
    Dumps all buttons/inputs/links from the shipment page to a text file.
    Use on first run to identify real Amazon selectors.
    """
    navigate_to_shipment(page, fba_id, base_url)
    output = [f"URL: {page.url}\nTitle: {page.title()}\n\n"]

    output.append("=== BUTTONS ===\n")
    for el in page.query_selector_all("button"):
        try:
            output.append(
                f"  text='{el.text_content().strip()}' | "
                f"class='{el.get_attribute('class')}' | "
                f"data-testid='{el.get_attribute('data-testid')}'\n"
            )
        except Exception:
            pass

    output.append("\n=== INPUTS ===\n")
    for el in page.query_selector_all("input"):
        try:
            output.append(
                f"  type='{el.get_attribute('type')}' | "
                f"name='{el.get_attribute('name')}' | "
                f"placeholder='{el.get_attribute('placeholder')}' | "
                f"aria-label='{el.get_attribute('aria-label')}'\n"
            )
        except Exception:
            pass

    dump = Path(logs_folder) / f"page_discovery_{fba_id}.txt"
    dump.write_text("".join(output), encoding="utf-8")
    print(f"\nDiscovery saved to: {dump}")
    print("Review BUTTONS section — update SELECTORS['add_tracking_button'] in upload_tracking.py if needed.")


def upload_all_shipments(shipments: dict, config: dict, page) -> list:
    """
    Uploads sub-tracking IDs to Amazon for each FBA shipment.
    shipments: {"FBA123": ["sub_id1", "sub_id2"], ...}
    Returns list of per-shipment result dicts.
    """
    base_url = config.get("amazon_base_url", "https://sellercentral.amazon.com")
    delay = config.get("delay_between_shipments_seconds", 2)
    results = []

    for fba_id, sub_ids in shipments.items():
        logger.info(f"\nFBA {fba_id}: {len(sub_ids)} sub-tracking IDs")
        r = {"fba_id": fba_id, "status": "success", "total": len(sub_ids),
             "succeeded": 0, "already_existed": 0, "failed": 0, "tracking_results": []}

        if not sub_ids:
            r["status"] = "skipped"
            results.append(r)
            continue

        if not navigate_to_shipment(page, fba_id, base_url):
            r["status"] = "not_found"
            results.append(r)
            continue

        for tid in sub_ids:
            tr = add_single_tracking_number(page, tid, fba_id, config)
            r["tracking_results"].append(tr)
            if tr["status"] == "success":
                r["succeeded"] += 1
            elif tr["status"] == "already_exists":
                r["already_existed"] += 1
            else:
                r["failed"] += 1

        if r["failed"] > 0 and r["succeeded"] == 0:
            r["status"] = "failed"
        elif r["failed"] > 0:
            r["status"] = "partial"

        results.append(r)
        time.sleep(delay)

    return results
```

**Step 2: Commit**

```bash
git add upload_tracking.py
git commit -m "feat: add Amazon Seller Central Playwright automation"
```

---

### Task 5: `run.py` — Main orchestrator

**Files:**
- Create: `ROOT\run.py`

**Step 1: Create `ROOT\run.py`**

```python
# run.py
import json
import logging
import os
import sys
import shutil
import argparse
from datetime import datetime
from pathlib import Path


def setup_logging(logs_folder: str) -> None:
    Path(logs_folder).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = Path(logs_folder) / f"tracking_upload_{ts}.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger(__name__).info(f"Log: {log_file}")


def load_config(config_path: str = "config.json") -> dict:
    if not Path(config_path).exists():
        print(f"ERROR: config.json not found. Expected at: {config_path}")
        sys.exit(1)
    with open(config_path) as f:
        config = json.load(f)
    defaults = {
        "amazon_base_url": "https://sellercentral.amazon.com",
        "headless": False,
        "delay_between_shipments_seconds": 2,
        "delay_between_tracking_numbers_seconds": 1,
        "column_fc_code": 3, "column_fba_id": 4,
        "column_tracking": 7, "column_carrier": 8,
        "us_fc_codes_file": "us_fc_codes.txt",
    }
    for k, v in defaults.items():
        config.setdefault(k, v)
    return config


def ensure_folders(config: dict) -> None:
    for key in ["input_folder", "output_folder", "logs_folder"]:
        Path(config[key]).mkdir(parents=True, exist_ok=True)
    Path(config["logs_folder"]).joinpath("screenshots").mkdir(parents=True, exist_ok=True)


def move_processed_files(config: dict) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for pattern in ["*.xls", "*.xlsx"]:
        for f in Path(config["input_folder"]).glob(pattern):
            dest = Path(config["output_folder"]) / f"{f.stem}_processed_{ts}{f.suffix}"
            shutil.move(str(f), str(dest))
            logging.getLogger(__name__).info(f"Moved {f.name} → {dest.name}")


def write_summary(results: list, logs_folder: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")
    icons = {"success": "[OK]     ", "partial": "[PARTIAL]",
             "failed": "[FAILED] ", "not_found": "[NOTFOUND]", "skipped": "[SKIP]   "}

    lines = [
        "=" * 60,
        f"TRACKING UPLOAD SUMMARY — {ts}",
        "=" * 60,
        f"Total FBA shipments: {len(results)}",
        f"  Successful:   {sum(1 for r in results if r['status'] == 'success')}",
        f"  Partial:      {sum(1 for r in results if r['status'] == 'partial')}",
        f"  Failed:       {sum(1 for r in results if r['status'] in ('failed','not_found'))}",
        f"  Skipped:      {sum(1 for r in results if r['status'] == 'skipped')}",
        "", "DETAILS:",
    ]
    for r in results:
        icon = icons.get(r["status"], "[?]")
        lines.append(
            f"  {icon} {r['fba_id']}  — "
            f"{r.get('succeeded',0)} added, "
            f"{r.get('already_existed',0)} existed, "
            f"{r.get('failed',0)} failed "
            f"(of {r.get('total',0)})"
        )
    lines.append("=" * 60)

    report = Path(logs_folder) / f"summary_{ts_file}.txt"
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover", action="store_true",
                        help="Dump Amazon page elements to logs/ (first-run only)")
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    config = load_config(args.config)
    ensure_folders(config)
    setup_logging(config["logs_folder"])

    from parse_excel import parse_and_filter
    from fetch_sub_tracking import get_all_sub_tracking
    from upload_tracking import (
        create_browser_context, check_login_status,
        discover_page_elements, upload_all_shipments,
    )

    logger = logging.getLogger(__name__)

    # Parse Excel
    logger.info("Reading Excel file...")
    shipments_raw = parse_and_filter(config)
    if not shipments_raw:
        print(f"\nNo US shipments found.")
        print(f"  • Drop Excel file in: {config['input_folder']}")
        print(f"  • Check column D has US FC codes (e.g. BNA, PHX)")
        input("\nPress Enter to exit...")
        return

    total_main = sum(len(v) for v in shipments_raw.values())
    print(f"\nFound {len(shipments_raw)} US FBA shipments, {total_main} main tracking numbers")

    # Launch browser
    logger.info("Launching Chrome...")
    try:
        pw, context = create_browser_context(config)
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        input("\nPress Enter to exit...")
        return

    page = context.new_page()

    try:
        # Discover mode
        if args.discover:
            first_fba = next(iter(shipments_raw))
            check_login_status(page, config["amazon_base_url"])
            discover_page_elements(page, first_fba, config["amazon_base_url"], config["logs_folder"])
            input("\nDiscovery complete. Press Enter to exit...")
            return

        # Step 1: Fetch sub-tracking IDs from carrier sites
        print("\n[1/2] Fetching sub-package tracking IDs from UPS/FedEx...")
        shipments_with_subs = {}
        for fba_id, entries in shipments_raw.items():
            logger.info(f"\nFBA {fba_id}: fetching {len(entries)} main tracking entries")
            sub_ids = get_all_sub_tracking(page, entries, config["logs_folder"])
            logger.info(f"  → {len(sub_ids)} sub-IDs collected")
            shipments_with_subs[fba_id] = sub_ids

        # Step 2: Upload to Amazon
        print("\n[2/2] Uploading to Amazon Seller Central...")
        check_login_status(page, config["amazon_base_url"])
        results = upload_all_shipments(shipments_with_subs, config, page)

    finally:
        context.close()
        pw.stop()

    move_processed_files(config)
    write_summary(results, config["logs_folder"])
    input("\nPress Enter to close...")


if __name__ == "__main__":
    main()
```

**Step 2: Commit**

```bash
git add run.py
git commit -m "feat: add run.py main orchestrator"
```

---

### Task 6: `setup.bat` and `run.bat`

**Files:**
- Create: `ROOT\setup.bat`
- Create: `ROOT\run.bat`

**Step 1: Create `ROOT\setup.bat`**

```batch
@echo off
title Setup - Amazon FBA Tracking Upload
color 0B
echo ============================================================
echo  One-Time Setup for Amazon FBA Tracking Upload
echo ============================================================
echo.
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Install from https://www.python.org/downloads/
    echo CHECK "Add Python to PATH" during install, then re-run this.
    pause & exit /b 1
)

echo [1/3] Installing Python packages...
pip install -r requirements.txt
if errorlevel 1 ( echo ERROR: pip install failed. & pause & exit /b 1 )

echo.
echo [2/3] Installing Playwright Chrome driver...
python -m playwright install chrome
if errorlevel 1 ( echo ERROR: Playwright install failed. & pause & exit /b 1 )

echo.
echo [3/3] Creating folders...
if not exist "input" mkdir input
if not exist "output" mkdir output
if not exist "logs" mkdir logs
if not exist "logs\screenshots" mkdir logs\screenshots

echo.
echo ============================================================
echo  Setup complete!
echo  Next steps:
echo  1. Open Chrome, log in to sellercentral.amazon.com, close Chrome
echo  2. Drop your Excel file into the 'input' folder
echo  3. Double-click run.bat
echo ============================================================
pause
```

**Step 2: Create `ROOT\run.bat`**

```batch
@echo off
title Amazon FBA Tracking Upload
color 0A
echo ============================================================
echo  Amazon FBA Tracking Number Uploader
echo ============================================================
echo.
echo  IMPORTANT: Close Google Chrome before continuing.
echo  (This tool opens Chrome automatically with your saved login.)
echo.
pause

cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not installed. Run setup.bat first.
    pause & exit /b 1
)

python run.py
echo.
pause
```

**Step 3: Commit**

```bash
git add setup.bat run.bat
git commit -m "feat: add user-facing run.bat and setup.bat"
```

---

### Task 7: First-run end-to-end verification (manual)

**Step 1: Run setup**

Double-click `setup.bat`. Expected: all packages install, no errors.

**Step 2: Log in to Amazon in Chrome**

Open Chrome → go to `https://sellercentral.amazon.com` → log in → close Chrome completely.

**Step 3: Drop the Excel file**

Copy `shipments  details  26-2.1(2).xls` into the `input\` folder.

**Step 4: Run discovery mode (first time only)**

```bash
cd "C:\Users\hadis\OneDrive\Desktop\Automated Tracking Tracking"
python run.py --discover
```

Expected: Chrome opens, navigates to first FBA shipment page, writes `logs\page_discovery_FBAxxxxxx.txt`.

**Step 5: Review discovery output**

Open `logs\page_discovery_FBAxxxxxx.txt`. Find the BUTTONS section. If the "Add tracking" button text differs from `'Add tracking'`, update the first entry in `SELECTORS["add_tracking_button"]` in `upload_tracking.py`.

**Step 6: Full run**

Double-click `run.bat`. Expected flow:
1. Chrome opens using your saved Amazon login
2. Visits UPS/FedEx pages → collects sub-package tracking IDs
3. Navigates to each FBA shipment URL → uploads tracking numbers
4. Prints summary to console
5. Excel moved to `output\`
6. Summary report written to `logs\`

**Step 7: Verify on Amazon**

Open one FBA shipment in Seller Central → confirm tracking numbers appear on the shipmentEvents page.

**Step 8: Final commit**

```bash
git add .
git commit -m "chore: add docs and verify end-to-end"
```

---

## Key Notes for Implementer

| Topic | Detail |
|---|---|
| Column indices | 0-based. D=3, E=4, H=7, I=8. Configurable in `config.json`. |
| US FC filter | First 3 letters of FC code checked against `us_fc_codes.txt`. Add missing prefixes there. |
| Carrier scraping | CSS selectors tried first; regex fallback always works (`1Z[0-9A-Z]{16}` for UPS, `\d{12,22}` for FedEx). |
| Amazon selectors | Run `--discover` first run to get real button/input selectors. Update `SELECTORS` if needed. |
| Chrome must be closed | `launch_persistent_context` requires exclusive profile access. |
| Browser is visible | `headless: false` so user can see actions and solve CAPTCHAs. |
| No carrier name needed | Amazon upload only needs the tracking number string. |
