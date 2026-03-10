# Amazon FBA Tracking Number Uploader

Automates uploading carrier tracking numbers from a supplier Excel file to Amazon Seller Central FBA shipments.

---

## What It Does

1. Reads your supplier Excel file from the `input/` folder
2. Filters rows where the destination is a US Amazon FC (e.g. BNA, GYR, IND)
3. For each FBA shipment, opens UPS/FedEx tracking pages to collect individual box tracking numbers
4. Logs in to Amazon Seller Central and fills in the tracking numbers on each shipment page
5. Saves a summary report and moves the Excel file to `output/`

---

## First-Time Setup (do this once)

**Requirements:** Windows PC with Google Chrome installed.

1. Install Python from https://www.python.org/downloads/
   - On the installer, check **"Add Python to PATH"**

2. Double-click **`setup.bat`**
   - Installs all required packages
   - Creates the input/output/logs folders
   - Writes your Chrome profile path to config.json automatically

That's it. You do **not** need to pre-log-in to Amazon — the script will open the browser and wait for you to log in on first run, then save your session for future runs.

---

## Every Week (normal usage)

1. Drop the supplier Excel file into the **`input/`** folder
2. Double-click **`run.bat`**
3. Close Chrome if prompted (the script needs exclusive access)
4. Log in to Amazon Seller Central when the browser window opens (first run only — session is saved after that)
5. Watch the progress in the console window
6. Review the summary report in `logs/`

---

## Excel File Format

The script reads these columns (0-indexed, no header assumptions):

| Column | Letter | What it reads |
|--------|--------|---------------|
| 3      | D      | FC destination code (e.g. GYR3, BNA6) |
| 4      | E      | FBA Shipment ID (e.g. FBA197HGGQXC) |
| 7      | H      | Main tracking number (UPS or FedEx) |
| 8      | I      | Carrier name (UPS / FedEx) |

Only rows where column D starts with a known US FC code are processed. See `us_fc_codes.txt` to add new FC codes.

---

## Command-Line Options

Run from the project folder:

```
python run.py                          # Full run: scrape carrier sites + upload to Amazon
python run.py --skip-carrier           # Skip UPS/FedEx — upload main tracking numbers directly
python run.py --only-fba FBA197HGGQXC # Run for one specific FBA shipment only
python run.py --discover               # Dump Amazon page elements to logs/ (for debugging)
```

---

## Output Files

| File | Description |
|------|-------------|
| `logs/summary_YYYYMMDD_HHMMSS.txt` | Human-readable upload results |
| `logs/tracking_ids_YYYYMMDD_HHMMSS.json` | All parent + sub tracking IDs collected |
| `logs/tracking_upload_YYYY-MM-DD.log` | Full debug log |
| `logs/screenshots/` | Screenshots taken at key steps |
| `output/filename_processed_TIMESTAMP.xlsx` | Your Excel file after processing |

---

## Summary Status Codes

| Code | Meaning |
|------|---------|
| `[OK]` | All tracking numbers uploaded successfully |
| `[PARTIAL]` | Some uploaded, some failed |
| `[SKIP]` | All boxes were already filled — nothing new to enter |
| `[FAILED]` | Could not upload (page not found, or all failed) |

---

## Troubleshooting

**"Chrome profile is already in use"**
→ Close Google Chrome completely, then run again.

**"No US FBA shipments found"**
→ Check that column D in your Excel has US FC codes (e.g. GYR, BNA, IND).
→ Check `us_fc_codes.txt` has the right 3-letter prefixes.

**Browser opens but shows login page every time**
→ The Chrome profile at `chrome_profile_path` in config.json may have been deleted.
→ Run setup.bat again to reset.

**UPS/FedEx shows CAPTCHA**
→ Solve it manually in the browser window, then press Enter in the console.

**Wrong tracking numbers uploaded**
→ The script only reads the "Other Packages in this Shipment" section on UPS pages.
→ Do NOT use API-intercepted responses — they contain unrelated recently-browsed numbers.

---

## Files

```
├── run.bat                  ← Double-click to run each week
├── setup.bat                ← Double-click once to set up
├── run.py                   ← Main script
├── parse_excel.py           ← Reads and filters the Excel file
├── fetch_sub_tracking.py    ← Scrapes UPS/FedEx for sub-package IDs
├── upload_tracking.py       ← Uploads to Amazon Seller Central via browser
├── config.json              ← Settings (edit chrome_profile_path if needed)
├── us_fc_codes.txt          ← List of US Amazon FC 3-letter codes
├── requirements.txt         ← Python package list
├── input/                   ← Drop Excel files here
├── output/                  ← Processed Excel files moved here
└── logs/                    ← Logs, summaries, screenshots
```
