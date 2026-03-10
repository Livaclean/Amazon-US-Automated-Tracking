# Multi-Region Support Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Amazon FBA Tracking Uploader to support CA, UK, and EU marketplaces in addition to US, all running sequentially in one command sharing a single carrier scrape pass.

**Architecture:** Each region has its own FC codes file and Amazon URL. `parse_excel.py` gains a `parse_and_filter_by_region()` function that returns shipments grouped by region. `run.py` loops through configured regions, checks login per region (pausing up to 5 min for manual login), uploads, and writes per-region summary files.

**Tech Stack:** Python 3, Playwright, openpyxl/xlrd, argparse (already in use)

---

## Chunk 1: FC Code Files + Config

### Task 1: Create `fc_codes/` directory with all four region files

**Files:**
- Create: `fc_codes/us_fc_codes.txt`
- Create: `fc_codes/ca_fc_codes.txt`
- Create: `fc_codes/uk_fc_codes.txt`
- Create: `fc_codes/eu_fc_codes.txt`
- Modify: `config.json.example`
- Modify: `.gitignore` (ensure `fc_codes/` is NOT ignored — these files must be committed)

- [ ] **Step 1: Create `fc_codes/` directory and `us_fc_codes.txt`**

Copy the existing `us_fc_codes.txt` content verbatim into `fc_codes/us_fc_codes.txt`. Do NOT delete `us_fc_codes.txt` from the root yet — it will be removed in Task 3 after the code is updated.

File: `fc_codes/us_fc_codes.txt`
```
# US Amazon Fulfillment Center 3-letter prefixes
# One prefix per line. Lines starting with # are comments.
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

- [ ] **Step 2: Create `fc_codes/ca_fc_codes.txt`**

File: `fc_codes/ca_fc_codes.txt`
```
# Canada Amazon Fulfillment Center 3-letter prefixes
YVR
YYZ
PRTO
YYC
YOW
YEG
YUL
```

- [ ] **Step 3: Create `fc_codes/uk_fc_codes.txt`**

File: `fc_codes/uk_fc_codes.txt`
```
# UK Amazon Fulfillment Center 3-letter prefixes
BHX
CWL
EDI
EUK
GLA
LBA
LCY
LTN
MAN
XUK
```

- [ ] **Step 4: Create `fc_codes/eu_fc_codes.txt`**

File: `fc_codes/eu_fc_codes.txt`
```
# EU Amazon Fulfillment Center 3-letter prefixes
# Germany
BER
CGN
DTM
DUS
EDE
FRA
HAM
LEJ
MUC
STR
XDE
# France
CDG
LYS
ORY
MRS
NTE
BOD
TLS
# Spain
BCN
MAD
SVQ
XES
# Italy
MXP
FCO
TRN
XIT
# Poland
KTW
POZ
SZZ
WRO
LCJ
# Czech Republic
PRG
```

- [ ] **Step 5: Update `config.json.example`**

Replace the entire file content with:
```json
{
  "input_folder": "input",
  "output_folder": "output",
  "logs_folder": "logs",
  "chrome_profile_path": "C:\\Users\\YOU\\AppData\\Local\\AmazonTrackingChrome",
  "chrome_profile_name": "Default",
  "amazon_base_url": "https://sellercentral.amazon.com",
  "headless": false,
  "delay_between_shipments_seconds": 2,
  "delay_between_tracking_numbers_seconds": 1,
  "column_fc_code": 3,
  "column_fba_id": 4,
  "column_tracking": 7,
  "column_carrier": 8,
  "regions": [
    { "name": "US", "amazon_url": "https://sellercentral.amazon.com",   "fc_codes_file": "fc_codes/us_fc_codes.txt" },
    { "name": "CA", "amazon_url": "https://sellercentral.amazon.ca",    "fc_codes_file": "fc_codes/ca_fc_codes.txt" },
    { "name": "UK", "amazon_url": "https://sellercentral.amazon.co.uk", "fc_codes_file": "fc_codes/uk_fc_codes.txt" },
    { "name": "EU", "amazon_url": "https://sellercentral.amazon.de",    "fc_codes_file": "fc_codes/eu_fc_codes.txt" }
  ]
}
```

Note: `amazon_base_url` and `us_fc_codes_file` are removed from the example. They still work as defaults in `load_config()` for backward compatibility with old configs.

- [ ] **Step 6: Verify `.gitignore` does NOT ignore `fc_codes/`**

Open `.gitignore` and confirm there is no line that would exclude `fc_codes/`. If `fc_codes/` is not listed, no change needed. The directory must be committed.

- [ ] **Step 7: Commit**

```bash
git add fc_codes/us_fc_codes.txt fc_codes/ca_fc_codes.txt fc_codes/uk_fc_codes.txt fc_codes/eu_fc_codes.txt config.json.example
git commit -m "feat: add fc_codes/ directory with US/CA/UK/EU prefix files and update config.json.example"
```

---

## Chunk 2: parse_excel.py — Region-Aware Parsing

### Task 2: Generalize FC prefix loading and add `parse_and_filter_by_region()`

**Files:**
- Modify: `parse_excel.py`
- Test: `tests/test_parse_excel.py`

The goal is to add a generic `load_fc_prefixes(file)` function and a `parse_and_filter_by_region(config)` function that returns a dict keyed by region name. Keep `parse_and_filter()` working unchanged (backward compat for `--from-json` flow and single-region use).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_parse_excel.py`:

```python
import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from parse_excel import load_fc_prefixes, is_us_fc, parse_and_filter_by_region


def test_load_fc_prefixes_returns_set(tmp_path):
    f = tmp_path / "codes.txt"
    f.write_text("BNA\nPHX\n# comment\n\nIND\n")
    result = load_fc_prefixes(str(f))
    assert result == {"BNA", "PHX", "IND"}


def test_load_fc_prefixes_missing_file(tmp_path):
    result = load_fc_prefixes(str(tmp_path / "missing.txt"))
    assert result == set()


def test_load_fc_prefixes_handles_4letter_codes(tmp_path):
    # PRTO is a 4-letter Canadian prefix — should be stored as-is
    f = tmp_path / "ca.txt"
    f.write_text("YVR\nYYZ\nPRTO\n")
    result = load_fc_prefixes(str(f))
    assert "PRTO" in result


def test_parse_and_filter_by_region_returns_dict_keyed_by_region(tmp_path):
    # Build minimal config pointing to tmp fc_codes files
    us_file = tmp_path / "us.txt"
    ca_file = tmp_path / "ca.txt"
    us_file.write_text("BNA\n")
    ca_file.write_text("YVR\n")

    config = {
        "input_folder": str(tmp_path / "input"),
        "column_fc_code": 0,
        "column_fba_id": 1,
        "column_tracking": 2,
        "column_carrier": 3,
        "regions": [
            {"name": "US", "amazon_url": "https://sellercentral.amazon.com", "fc_codes_file": str(us_file)},
            {"name": "CA", "amazon_url": "https://sellercentral.amazon.ca",  "fc_codes_file": str(ca_file)},
        ],
    }

    # No Excel file — should return empty dicts for each region
    Path(config["input_folder"]).mkdir()
    result = parse_and_filter_by_region(config)
    assert "US" in result
    assert "CA" in result
    assert result["US"] == {}
    assert result["CA"] == {}
```

- [ ] **Step 2: Run tests to confirm they fail**

```
.venv\Scripts\python.exe -m pytest tests/test_parse_excel.py -v
```

Expected: FAIL — `load_fc_prefixes` and `parse_and_filter_by_region` not yet defined.

- [ ] **Step 3: Add `load_fc_prefixes()` to `parse_excel.py`**

In `parse_excel.py`, add this function right below `load_us_fc_prefixes()` (around line 29):

```python
def load_fc_prefixes(fc_codes_file: str) -> set:
    """Reads an FC codes file, returns set of uppercase prefixes (any length)."""
    prefixes = set()
    try:
        with open(fc_codes_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    prefixes.add(line.upper())
    except FileNotFoundError:
        logger.warning(f"FC codes file not found: {fc_codes_file}")
    return prefixes
```

Also update `is_us_fc()` to handle prefixes of any length (not just 3 letters). Rename it to `is_region_fc()` and add `is_us_fc` as an alias for backward compatibility:

Replace the existing `is_us_fc` function (lines 32-37) with:

```python
def is_region_fc(fc_code, prefixes: set) -> bool:
    """True if fc_code starts with any known FC prefix from the given set."""
    if not fc_code:
        return False
    fc_str = str(fc_code).strip().upper()
    return any(fc_str.startswith(p) for p in prefixes)


def is_us_fc(fc_code, us_prefixes: set) -> bool:
    """Backward-compatible alias for is_region_fc."""
    return is_region_fc(fc_code, us_prefixes)
```

- [ ] **Step 4: Add `parse_and_filter_by_region()` to `parse_excel.py`**

Add this function at the bottom of `parse_excel.py` (after `parse_and_filter`):

```python
def parse_and_filter_by_region(config: dict) -> dict:
    """
    Finds Excel files, loads all rows, then splits by region using each region's FC codes file.
    Returns: {"US": {"FBA123": [...]}, "CA": {"FBA456": [...]}, ...}
    Each region only contains FBA IDs whose FC code matches that region's prefixes.
    """
    regions = config.get("regions", [])
    if not regions:
        logger.warning("No 'regions' key in config — falling back to US-only parse_and_filter()")
        return {"US": parse_and_filter(config)}

    excel_files = find_excel_files(config["input_folder"])
    if not excel_files:
        logger.warning(f"No Excel files found in {config['input_folder']}")
        return {r["name"]: {} for r in regions}

    all_rows = []
    for file_path in excel_files:
        logger.info(f"Reading: {file_path}")
        rows = load_excel_file(file_path, config)
        all_rows.extend(rows)
    logger.info(f"Loaded {len(all_rows)} total rows across {len(excel_files)} file(s)")

    result = {}
    for region in regions:
        name = region["name"]
        fc_file = region.get("fc_codes_file", "")
        prefixes = load_fc_prefixes(fc_file)
        if not prefixes:
            logger.warning(f"[{name}] No FC prefixes loaded from {fc_file!r}")
        region_rows = [r for r in all_rows if is_region_fc(r["fc_code"], prefixes)]
        logger.info(f"[{name}] {len(region_rows)} row(s) matched")
        result[name] = group_by_fba_id(region_rows)

    return result
```

- [ ] **Step 5: Run tests to confirm they pass**

```
.venv\Scripts\python.exe -m pytest tests/test_parse_excel.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add parse_excel.py tests/test_parse_excel.py
git commit -m "feat: add load_fc_prefixes() and parse_and_filter_by_region() to parse_excel.py"
```

---

## Chunk 3: run.py — Multi-Region Loop

### Task 3: Add `--regions` flag and region loop to `run.py`

**Files:**
- Modify: `run.py`
- Delete: `us_fc_codes.txt` (root-level, replaced by `fc_codes/us_fc_codes.txt`)

This is the main orchestration change. The normal flow (no `--from-json`, no `--check-only`) becomes a region loop. Each region:
1. Parses its shipments from the already-loaded Excel rows
2. Scrapes carriers (carrier scrape is shared — deduplicated across all regions)
3. Checks Amazon login for that region's URL (pauses up to 5 min if not logged in)
4. Uploads to that region's Amazon URL
5. Writes a per-region summary to `logs/summary_<REGION>_<timestamp>.txt`

The `--from-json` and `--check-only` paths are US-only (unchanged for now).

- [ ] **Step 1: Add `--regions` flag to the argument parser in `run.py`**

In `main()`, add this after the existing `--from-json` argument (around line 191):

```python
parser.add_argument(
    "--regions",
    nargs="+",
    default=None,
    metavar="REGION",
    help="Limit which regions to run (e.g. --regions US CA). Default: all regions in config.",
)
```

- [ ] **Step 2: Add `write_region_summary()` function to `run.py`**

Add this function after `write_summary()` (around line 104):

```python
def write_region_summary(region_name: str, results: list, logs_folder: str, timestamp: str) -> None:
    """Writes a per-region summary file to logs/summary_<REGION>_<timestamp>.txt."""
    icons = {
        "success": "[OK]      ",
        "partial": "[PARTIAL] ",
        "failed": "[FAILED]  ",
        "not_found": "[NOTFOUND]",
        "skipped": "[SKIP]    ",
    }
    lines = [
        "=" * 60,
        f"REGION: {region_name} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
        f"Total FBA shipments: {len(results)}",
        f"  Successful: {sum(1 for r in results if r['status'] == 'success')}",
        f"  Partial:    {sum(1 for r in results if r['status'] == 'partial')}",
        f"  Failed:     {sum(1 for r in results if r['status'] in ('failed', 'not_found'))}",
        f"  Skipped:    {sum(1 for r in results if r['status'] == 'skipped')}",
        "",
        "DETAILS:",
    ]
    for r in results:
        icon = icons.get(r["status"], "[?]       ")
        lines.append(
            f"  {icon} {r['fba_id']}  - "
            f"{r.get('succeeded', 0)} added, "
            f"{r.get('already_existed', 0)} existed, "
            f"{r.get('failed', 0)} failed "
            f"(of {r.get('total', 0)})"
        )
    lines.append("=" * 60)

    report = Path(logs_folder) / f"summary_{region_name}_{timestamp}.txt"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[{region_name}] Summary saved: {report.name}")
```

- [ ] **Step 3: Add `wait_for_login()` helper function to `run.py`**

Add this function after `write_region_summary()`:

```python
def wait_for_login(page, region_name: str, amazon_url: str, timeout_seconds: int = 300) -> bool:
    """
    Navigates to amazon_url, checks login status.
    If not logged in, prints a prompt and polls every 5 seconds for up to timeout_seconds.
    Returns True if logged in (or already was), False if timed out.
    """
    from upload_tracking import check_login_status
    import time

    logger = logging.getLogger(__name__)
    page.goto(amazon_url, wait_until="domcontentloaded", timeout=30000)
    page_text = page.inner_text("body")

    # Simple logged-in check: Seller Central shows "Hello, " or the dashboard nav
    logged_in = (
        "sign in" not in page_text.lower()
        and "sign-in" not in page_text.lower()
        and ("hello" in page_text.lower() or "seller central" in page_text.lower())
    )

    if logged_in:
        logger.info(f"[{region_name}] Already logged in at {amazon_url}")
        return True

    print(f"\n[{region_name}] NOT logged in at {amazon_url}")
    print(f"[{region_name}] Please log in manually in the browser. Waiting up to {timeout_seconds // 60} minutes...")

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(5)
        try:
            page.goto(amazon_url, wait_until="domcontentloaded", timeout=15000)
            page_text = page.inner_text("body")
            logged_in = (
                "sign in" not in page_text.lower()
                and "sign-in" not in page_text.lower()
                and ("hello" in page_text.lower() or "seller central" in page_text.lower())
            )
            if logged_in:
                print(f"[{region_name}] Login detected! Proceeding...")
                return True
        except Exception:
            pass

    logger.warning(f"[{region_name}] Login timed out after {timeout_seconds}s — skipping region")
    print(f"[{region_name}] Login timed out — skipping this region.")
    return False
```

- [ ] **Step 4: Replace the normal flow in `main()` with a region loop**

In `run.py`, locate the section starting at `# Step 1: Parse Excel` (around line 217). Replace the normal (non-`--from-json`, non-`--check-only`) flow with region-aware logic.

The key changes:

**4a.** Replace the import line at the top of `main()`:
```python
from parse_excel import parse_and_filter, categorize_shipments
```
with:
```python
from parse_excel import parse_and_filter, parse_and_filter_by_region, categorize_shipments
```

**4b.** After `ensure_folders(config)` and logging setup, determine the active regions:

```python
# Determine which regions to run
configured_regions = config.get("regions", [])
if not configured_regions:
    # Backward compat: no regions in config → US only using amazon_base_url
    configured_regions = [{
        "name": "US",
        "amazon_url": config.get("amazon_base_url", "https://sellercentral.amazon.com"),
        "fc_codes_file": config.get("us_fc_codes_file", "us_fc_codes.txt"),
    }]

if args.regions:
    allowed = set(args.regions)
    configured_regions = [r for r in configured_regions if r["name"] in allowed]
    if not configured_regions:
        print(f"\nERROR: None of the specified --regions ({args.regions}) found in config.")
        return
```

**4c.** Replace the `parse_and_filter(config)` call with `parse_and_filter_by_region(config)`:

```python
logger.info("Reading Excel file from input folder...")
all_regions_data = parse_and_filter_by_region(config)
```

**4d.** Apply `--only-fba` and `--fba-list` filters to each region's data:

```python
if args.only_fba:
    for name in all_regions_data:
        if args.only_fba in all_regions_data[name]:
            all_regions_data[name] = {args.only_fba: all_regions_data[name][args.only_fba]}
        else:
            all_regions_data[name] = {}
    found_in = [n for n in all_regions_data if all_regions_data[n]]
    if not found_in:
        print(f"\nERROR: FBA ID '{args.only_fba}' not found in any region.")
        return
    print(f"\nRunning for single shipment: {args.only_fba} (found in: {', '.join(found_in)})")

if args.fba_list:
    fba_list_path = Path(args.fba_list)
    if not fba_list_path.exists():
        print(f"\nERROR: FBA list file not found: {args.fba_list}")
        return
    fba_ids = {line.strip() for line in fba_list_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    for name in all_regions_data:
        all_regions_data[name] = {fba: v for fba, v in all_regions_data[name].items() if fba in fba_ids}
    print(f"\nFiltered to FBA IDs from list: {fba_list_path.name}")
```

**4e.** Replace the single-region carrier scrape + upload with the region loop. The carrier scrape runs ONCE over all unique tracking entries across all regions. Then the loop visits each region for login + upload.

```python
# Combine all regions' shipments for a single carrier scrape pass
all_shipments_raw = {}
for region_data in all_regions_data.values():
    all_shipments_raw.update(region_data)

shipments_all = all_shipments_raw  # used later for highlight_excel row numbers

# Categorize for carrier scrape (has_tracking only)
shipments_raw_all, missing_tracking_all = categorize_shipments(all_shipments_raw)
write_shipment_records(shipments_raw_all, missing_tracking_all, config["logs_folder"])

if missing_tracking_all:
    print(f"\n  {len(missing_tracking_all)} FBA(s) have no usable tracking — recorded to logs.")

total_main = sum(len(v) for v in shipments_raw_all.values())
print(f"\nFound {sum(len(d) for d in all_regions_data.values())} FBA shipments total "
      f"({total_main} with tracking) across {len(configured_regions)} region(s).")
```

**4f.** Launch browser and run carrier scrape (same as current flow, but only once):

```python
logger.info("Launching Chrome with your saved profile...")
try:
    pw, context = create_browser_context(config)
except RuntimeError as e:
    print(f"\nERROR: {e}")
    try:
        input("\nPress Enter to exit...")
    except EOFError:
        pass
    return

page = context.new_page()
all_results = []

try:
    if args.skip_carrier:
        print("\n[1/2] Skipping carrier scraping — using main tracking numbers directly...")
        shipments_with_subs = {}
        for fba_id, entries in shipments_raw_all.items():
            main_ids = [e["tracking"] for e in entries if e.get("tracking")]
            shipments_with_subs[fba_id] = main_ids
    else:
        has_fedex = any(
            "fedex" in str(e.get("carrier", "")).lower()
            for entries in shipments_raw_all.values()
            for e in entries
        )
        if has_fedex:
            print("\n[FedEx] Checking FedEx login...")
            check_fedex_login(page)

        print("\n[1/2] Fetching sub-package tracking IDs from UPS/FedEx...")
        shipments_with_subs = {}
        for fba_id, entries in shipments_raw_all.items():
            logger.info(f"\nFBA {fba_id}: {len(entries)} main tracking entries")
            main_ids = [e["tracking"] for e in entries if e.get("tracking")]
            sub_ids = get_all_sub_tracking(page, entries, config["logs_folder"])
            all_ids = list(dict.fromkeys(main_ids + sub_ids))
            logger.info(f"  -> {len(all_ids)} total tracking IDs ({len(main_ids)} main + {len(sub_ids)} sub)")
            shipments_with_subs[fba_id] = all_ids

    # Save tracking IDs JSON
    ts_ids = datetime.now().strftime("%Y%m%d_%H%M%S")
    tracking_ids_file = Path(config["logs_folder"]) / f"tracking_ids_{ts_ids}.json"
    combined = {
        fba_id: {
            "parent": shipments_raw_all.get(fba_id, []),
            "sub_ids": shipments_with_subs.get(fba_id, []),
        }
        for fba_id in set(list(shipments_raw_all.keys()) + list(shipments_with_subs.keys()))
    }
    with open(tracking_ids_file, "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2)
    print(f"\nTracking IDs saved to: {tracking_ids_file}")

    if args.collect_only:
        print(f"\n{'='*60}\nTRACKING COLLECTION COMPLETE (nothing uploaded)\n{'='*60}")
        for fba_id, data in sorted(combined.items()):
            main = [e["tracking"] for e in data.get("parent", []) if e.get("tracking")]
            subs = data.get("sub_ids", [])
            print(f"\n  {fba_id}")
            print(f"    Main : {', '.join(main) if main else '(none)'}")
            print(f"    Subs : {', '.join(subs) if subs else '(none)'}")
        print(f"\n{'='*60}")
        return

    # Step 2: Region loop — login + upload for each region
    print(f"\n[2/2] Uploading to Amazon ({len(configured_regions)} region(s))...")
    ts_run = datetime.now().strftime("%Y%m%d_%H%M%S")
    region_results_map = {}  # region_name -> list of result dicts

    for region in configured_regions:
        region_name = region["name"]
        amazon_url = region["amazon_url"]

        # Get this region's FBA IDs
        region_fba_ids = set(all_regions_data.get(region_name, {}).keys())
        region_shipments = {fba: shipments_with_subs[fba] for fba in region_fba_ids if fba in shipments_with_subs}

        if not region_shipments:
            print(f"\n[{region_name}] No shipments to upload — skipping.")
            region_results_map[region_name] = []
            continue

        print(f"\n{'='*60}")
        print(f"[{region_name}] {len(region_shipments)} shipment(s) — {amazon_url}")
        print(f"{'='*60}")

        # Per-region login check
        logged_in = wait_for_login(page, region_name, amazon_url, timeout_seconds=300)
        if not logged_in:
            region_results_map[region_name] = []
            write_region_summary(region_name, [], config["logs_folder"], ts_run)
            continue

        # Upload for this region
        region_config = dict(config)
        region_config["amazon_base_url"] = amazon_url
        region_results = upload_all_shipments(region_shipments, region_config, page)
        region_results_map[region_name] = region_results
        all_results.extend(region_results)
        write_region_summary(region_name, region_results, config["logs_folder"], ts_run)

    # Post-run: highlight Excel rows
    ts_out = datetime.now().strftime("%Y%m%d_%H%M%S")
    input_folder = Path(config["input_folder"])
    output_folder = Path(config["output_folder"])
    updated_rows = collect_updated_row_numbers(shipments_all, all_results)

    for pattern in ["*.xls", "*.xlsx"]:
        for src_file in input_folder.glob(pattern):
            dest_file = output_folder / f"{src_file.stem}_processed_{ts_out}.xlsx"
            actual_dest = highlight_and_save(str(src_file), str(dest_file), updated_rows)
            if Path(actual_dest).exists():
                try:
                    src_file.unlink()
                    print(f"Output saved with highlights: {Path(actual_dest).name}")
                except PermissionError:
                    print(f"Output saved: {Path(actual_dest).name} (input file still open — close it manually)")
            else:
                print(f"WARNING: Could not confirm output file — input kept: {src_file.name}")

    # Combined cross-region summary
    write_summary(all_results, config["logs_folder"])

finally:
    context.close()
    pw.stop()

try:
    input("\nPress Enter to close this window...")
except EOFError:
    pass
```

- [ ] **Step 5: Update `load_config()` defaults in `run.py`**

In `load_config()`, the `"us_fc_codes_file"` default should point to the new location. Update the defaults dict:

```python
defaults = {
    "amazon_base_url": "https://sellercentral.amazon.com",
    "headless": False,
    "delay_between_shipments_seconds": 2,
    "delay_between_tracking_numbers_seconds": 1,
    "column_fc_code": 3,
    "column_fba_id": 4,
    "column_tracking": 7,
    "column_carrier": 8,
    "us_fc_codes_file": "fc_codes/us_fc_codes.txt",  # updated path
}
```

- [ ] **Step 6: Delete root-level `us_fc_codes.txt`**

Now that all code points to `fc_codes/us_fc_codes.txt`, delete the old file:

```bash
git rm us_fc_codes.txt
```

- [ ] **Step 7: Smoke test — run with `--regions US` to verify US-only still works**

```
.venv\Scripts\python.exe run.py --skip-carrier --regions US
```

Expected: runs US region only, same behaviour as before v2.

- [ ] **Step 8: Commit**

```bash
git add run.py parse_excel.py
git rm us_fc_codes.txt
git commit -m "feat: add multi-region loop to run.py with per-region login wait and summary files"
```

---

## Chunk 4: Final Wiring + Tests

### Task 4: Integration test and config update

**Files:**
- Modify: `config.json` (user's local config — add regions block manually, not committed)
- Test: `tests/test_run_regions.py`

- [ ] **Step 1: Write integration smoke test for region filtering logic**

Create `tests/test_run_regions.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_region_filtering_excludes_other_regions():
    """parse_and_filter_by_region should put CA rows in CA, not US."""
    from parse_excel import load_fc_prefixes, is_region_fc

    us_prefixes = {"BNA", "PHX", "IND"}
    ca_prefixes = {"YVR", "YYZ", "PRTO"}

    rows = [
        {"fc_code": "BNA6", "fba_id": "FBA001", "tracking_num": "1Z000", "carrier": "UPS", "row_number": 2},
        {"fc_code": "YVR3", "fba_id": "FBA002", "tracking_num": "1Z111", "carrier": "UPS", "row_number": 3},
        {"fc_code": "PRTO5", "fba_id": "FBA003", "tracking_num": "1Z222", "carrier": "UPS", "row_number": 4},
    ]

    us_rows = [r for r in rows if is_region_fc(r["fc_code"], us_prefixes)]
    ca_rows = [r for r in rows if is_region_fc(r["fc_code"], ca_prefixes)]

    assert len(us_rows) == 1
    assert us_rows[0]["fba_id"] == "FBA001"
    assert len(ca_rows) == 2
    assert {r["fba_id"] for r in ca_rows} == {"FBA002", "FBA003"}


def test_4letter_prefix_matching():
    """PRTO prefix should match fc_code PRTO5."""
    from parse_excel import is_region_fc
    prefixes = {"PRTO", "YVR", "YYZ"}
    assert is_region_fc("PRTO5", prefixes)
    assert is_region_fc("YVR2", prefixes)
    assert not is_region_fc("BNA6", prefixes)
```

- [ ] **Step 2: Run the tests**

```
.venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 3: Update local `config.json` with regions block**

Manually edit `config.json` (not committed — it's in .gitignore) to add:

```json
"regions": [
  { "name": "US", "amazon_url": "https://sellercentral.amazon.com",   "fc_codes_file": "fc_codes/us_fc_codes.txt" },
  { "name": "CA", "amazon_url": "https://sellercentral.amazon.ca",    "fc_codes_file": "fc_codes/ca_fc_codes.txt" },
  { "name": "UK", "amazon_url": "https://sellercentral.amazon.co.uk", "fc_codes_file": "fc_codes/uk_fc_codes.txt" },
  { "name": "EU", "amazon_url": "https://sellercentral.amazon.de",    "fc_codes_file": "fc_codes/eu_fc_codes.txt" }
]
```

- [ ] **Step 4: Final commit and push**

```bash
git add tests/test_run_regions.py tests/test_parse_excel.py
git commit -m "test: add multi-region unit tests"
git push
```

---

## Usage After Implementation

```bash
# All regions (US → CA → UK → EU):
.venv\Scripts\python.exe run.py

# US only:
.venv\Scripts\python.exe run.py --regions US

# US and CA only:
.venv\Scripts\python.exe run.py --regions US CA

# Skip carrier scrape (upload main tracking numbers directly):
.venv\Scripts\python.exe run.py --skip-carrier

# EU only:
.venv\Scripts\python.exe run.py --regions EU
```

Per-region summary files are written to `logs/summary_US_<timestamp>.txt`, `logs/summary_CA_<timestamp>.txt`, etc. A combined summary is printed at the end.
