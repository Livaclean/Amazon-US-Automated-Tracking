# Amazon Automated Tracking Number Uploader

Automates uploading carrier tracking numbers from a supplier Excel file to Amazon Seller Central FBA/AWD shipments. Supports multiple regions (US, CA, UK, EU).

---

## What It Does

1. Reads your supplier Excel file from the `input/` folder
2. Filters rows by destination Amazon FC code, grouped by region
3. For each FBA/AWD shipment, opens UPS/FedEx tracking pages to collect individual box tracking numbers
4. Logs in to Amazon Seller Central and fills in the tracking numbers on each shipment page
5. Saves per-region summary reports and moves the Excel file to `output/`

---

## Supported Regions

| Region | Seller Central URL | FC Codes File |
|--------|-------------------|---------------|
| US | sellercentral.amazon.com | `fc_codes/us_fc_codes.txt` |
| CA | sellercentral.amazon.ca | `fc_codes/ca_fc_codes.txt` |
| UK | sellercentral.amazon.co.uk | `fc_codes/uk_fc_codes.txt` |
| EU | sellercentral.amazon.de | `fc_codes/eu_fc_codes.txt` |

AWD shipments (IDs starting with `STAR-`) are also supported and routed to the correct region.

---

## First-Time Setup (do this once)

**Requirements:** Windows PC with Google Chrome installed.

1. Install Python from https://www.python.org/downloads/
   - On the installer, check **"Add Python to PATH"**

2. Double-click **`setup.bat`**
   - Installs all required packages
   - Creates the input/output/logs folders
   - Writes your Chrome profile path to config.json automatically

You do **not** need to pre-log-in to Amazon — the script will open the browser and wait for you to log in on first run, then save your session for future runs.

---

## Every Week (normal usage)

1. Drop the supplier Excel file into the **`input/`** folder
2. Double-click **`run.bat`**
3. Close Chrome if prompted (the script needs exclusive access)
4. Log in to Amazon Seller Central when the browser window opens (first run only — session is saved after that)
5. Watch the progress in the console window
6. Review the summary reports in `logs/`

---

## Excel File Format

The script reads these columns (0-indexed, configurable in config.json):

| Column | Letter | What it reads |
|--------|--------|---------------|
| 3      | D      | FC destination code (e.g. GYR3, BNA6, YVR2) |
| 4      | E      | FBA/AWD Shipment ID (e.g. FBA197HGGQXC, STAR-ABC123) |
| 7      | H      | Main tracking number (UPS or FedEx) |
| 8      | I      | Carrier name (UPS / FedEx) |

Rows are matched to a region by checking whether the FC code prefix appears in that region's `fc_codes_file`. Rows with FBA IDs containing `/` (e.g. `STAR-A/STAR-B`) are split into separate shipments.

---

## Command-Line Options

```
python run.py                                    # Full run: scrape carriers + upload to Amazon (all regions)
python run.py --regions US CA                    # Run only US and CA regions
python run.py --skip-carrier                     # Skip UPS/FedEx — upload main tracking numbers directly
python run.py --only-fba FBA197HGGQXC            # Run for one specific FBA shipment only
python run.py --fba-list fba_ids.txt             # Limit to FBA IDs listed in a text file (one per line)
python run.py --collect-only                     # Fetch tracking numbers and save to JSON, don't upload
python run.py --from-json logs/tracking_ids.json # Upload from a previously saved JSON (skip Excel + carriers)
python run.py --check-only                       # Check Amazon tracking status without uploading
python run.py --rewrite                          # Force overwrite tracking inputs that already have values
python run.py --discover                         # Dump Amazon page elements to logs/ (for debugging)
python run.py --discover --fba-id FBA197HGGQXC   # Discover page elements for a specific FBA ID
python run.py --config path/to/config.json       # Use a different config file
```

| Flag | Description |
|------|-------------|
| `--regions REGION [...]` | Limit which regions to run (e.g. `--regions US CA`). Default: all in config. |
| `--skip-carrier` | Skip UPS/FedEx scraping, upload main tracking numbers directly. |
| `--only-fba ID` | Run for one specific FBA/AWD shipment ID only. |
| `--fba-list FILE` | Path to text file with FBA IDs (one per line) to limit processing. |
| `--collect-only` | Fetch main + sub tracking numbers, save JSON to `logs/`, don't upload. |
| `--from-json FILE` | Upload from a previously saved `tracking_ids_*.json` file. |
| `--check-only` | Check Amazon tracking status for each shipment without uploading. |
| `--rewrite` | Force overwrite tracking inputs that already have a value. |
| `--discover` | Dump Amazon page elements to `logs/` for debugging selectors. |
| `--fba-id ID` | Specific FBA ID for `--discover` (default: first from Excel). |
| `--config FILE` | Path to config.json (default: `config.json` in current directory). |

---

## Pre-Check Flow and Done Cache

Before uploading, the script checks each shipment's current tracking status on Amazon:
- **Already complete** shipments are skipped automatically and cached in `logs/completed_fba_<REGION>.txt`
- On subsequent runs, cached shipments are skipped without opening Amazon, saving time
- Use `--rewrite` to force re-upload even for completed shipments

---

## Output Files

| File | Description |
|------|-------------|
| `logs/summary_YYYYMMDD_HHMMSS.txt` | Overall upload results |
| `logs/summary_<REGION>_YYYYMMDD_HHMMSS.txt` | Per-region upload results |
| `logs/tracking_ids_YYYYMMDD_HHMMSS.json` | All parent + sub tracking IDs collected |
| `logs/shipments_with_tracking_*.txt` | FBA IDs that had tracking numbers |
| `logs/shipments_missing_tracking_*.txt` | FBA IDs with no tracking numbers |
| `logs/completed_fba_<REGION>.txt` | Persistent done cache per region |
| `logs/tracking_upload_YYYY-MM-DD.log` | Full debug log |
| `logs/screenshots/` | Screenshots taken at key steps |
| `output/filename_processed_TIMESTAMP.xlsx` | Highlighted Excel file after processing |

---

## Summary Status Codes

| Code | Meaning |
|------|---------|
| `[OK]` | All tracking numbers uploaded successfully |
| `[PARTIAL]` | Some uploaded, some failed |
| `[SKIP]` | All boxes were already filled — nothing new to enter |
| `[FAILED]` | Could not upload (page not found, or all failed) |

---

## Running Tests

```bash
# Run all unit tests (fast, no browser needed)
python -m pytest tests/ -m unit -v

# Run all tests (unit + integration + e2e)
python -m pytest tests/ -v

# Run integration tests only (needs config.json, Chrome, and test tracking numbers)
python -m pytest tests/ -m integration -v

# Run e2e pipeline tests only (needs config.json and Excel files in input/)
python -m pytest tests/ -m e2e -v
```

Integration and e2e tests skip cleanly when prerequisites are missing (no config, no browser, no tracking numbers). To run carrier integration tests, create `tests/fixtures/test_tracking_numbers.json` from the `.example` template with real tracking numbers.

---

## Troubleshooting

**"Chrome profile is already in use"**
> Close Google Chrome completely, then run again.

**"No FBA shipments found"**
> Check that column D in your Excel has valid FC codes.
> Check the appropriate file in `fc_codes/` has the right prefixes.

**Browser opens but shows login page every time**
> The Chrome profile at `chrome_profile_path` in config.json may have been deleted.
> Run setup.bat again to reset.

**UPS/FedEx shows CAPTCHA**
> Solve it manually in the browser window, then press Enter in the console.

**Wrong tracking numbers uploaded**
> The script only reads the "Other Packages in this Shipment" section on UPS pages.
> Do NOT use API-intercepted responses — they contain unrelated recently-browsed numbers.

---

## Files

```
Amazon-US-Automated-Tracking/
├── run.bat                     # Double-click to run each week
├── setup.bat                   # Double-click once to set up
├── run.py                      # Main script (orchestration + CLI)
├── parse_excel.py              # Reads and filters the Excel file by region
├── fetch_sub_tracking.py       # Scrapes UPS/FedEx for sub-package tracking IDs
├── upload_tracking.py          # Uploads to Amazon Seller Central via browser
├── highlight_excel.py          # Highlights processed rows in Excel output
├── config.json                 # Settings (created by setup.bat)
├── config.json.example         # Example config with all options
├── requirements.txt            # Python package list
├── fc_codes/
│   ├── us_fc_codes.txt         # US Amazon FC 3-letter prefixes
│   ├── ca_fc_codes.txt         # Canada FC prefixes
│   ├── uk_fc_codes.txt         # UK FC prefixes
│   ├── eu_fc_codes.txt         # EU FC prefixes
│   └── awd_fc_codes.txt        # AWD warehouse prefixes
├── input/                      # Drop Excel files here
├── output/                     # Processed Excel files moved here
├── logs/                       # Logs, summaries, screenshots, done caches
└── tests/
    ├── conftest.py             # Shared fixtures and test infrastructure
    ├── test_run_unit.py        # Unit tests for run.py utilities
    ├── test_parse_excel.py     # Unit tests for Excel parsing + filtering
    ├── test_highlight_excel.py # Unit tests for Excel highlighting
    ├── test_fetch_sub_tracking.py # Unit tests for carrier tracking extraction
    ├── test_run_regions.py     # Multi-region integration tests
    ├── test_carrier_integration.py # Real browser UPS/FedEx tests
    ├── test_amazon_integration.py  # Real browser Amazon SC tests
    ├── test_e2e_pipeline.py    # Subprocess pipeline tests
    └── fixtures/               # Test data files
```
