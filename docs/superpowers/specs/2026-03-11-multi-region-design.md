# Multi-Region Support Design
**Date:** 2026-03-11
**Status:** Approved

## Overview
Extend the Amazon FBA Tracking Uploader to support CA, UK, and EU marketplaces in addition to US. All regions run sequentially in a single command, sharing one carrier scrape pass.

## Regions
| Region | Amazon URL | FC Codes File |
|--------|-----------|---------------|
| US | https://sellercentral.amazon.com | fc_codes/us_fc_codes.txt |
| CA | https://sellercentral.amazon.ca | fc_codes/ca_fc_codes.txt |
| UK | https://sellercentral.amazon.co.uk | fc_codes/uk_fc_codes.txt |
| EU | https://sellercentral.amazon.de | fc_codes/eu_fc_codes.txt |

## File Structure Changes
```
fc_codes/
  us_fc_codes.txt   (moved from root)
  ca_fc_codes.txt   (new)
  uk_fc_codes.txt   (new)
  eu_fc_codes.txt   (new)
```

`config.json` gains a `regions` array:
```json
"regions": [
  { "name": "US", "amazon_url": "https://sellercentral.amazon.com",   "fc_codes_file": "fc_codes/us_fc_codes.txt" },
  { "name": "CA", "amazon_url": "https://sellercentral.amazon.ca",    "fc_codes_file": "fc_codes/ca_fc_codes.txt" },
  { "name": "UK", "amazon_url": "https://sellercentral.amazon.co.uk", "fc_codes_file": "fc_codes/uk_fc_codes.txt" },
  { "name": "EU", "amazon_url": "https://sellercentral.amazon.de",    "fc_codes_file": "fc_codes/eu_fc_codes.txt" }
]
```

A `--regions` flag limits which regions run:
```
python run.py                   # all regions
python run.py --regions US CA   # US and CA only
```

## Data Flow
```
Excel file (single file, all regions)
    ↓
parse_and_filter_by_region() → { "US": {...}, "CA": {...}, "UK": {...}, "EU": {...} }
    ↓
Carrier scrape ONCE (deduplicated across all regions)
    ↓
Sequential region loop (US → CA → UK → EU):
  1. Navigate to region Amazon URL
  2. Check login — pause and wait (up to 5 min) if session expired
  3. Upload shipments for this region
  4. Write per-region summary to logs/
    ↓
Combined summary printed at end
```

## Login Handling
- Each region has an independent session
- On entering each region: check if logged in via `check_login_status()`
- If not logged in: print prompt in terminal, browser stays open, wait up to 5 minutes for manual login
- After 5 minutes with no login detected: skip region, log warning, continue to next region
- Same Chrome profile used throughout (single browser instance)

## Error Handling
| Situation | Behaviour |
|-----------|-----------|
| Individual shipment fails | Log, mark failed, continue to next shipment |
| Entire region upload fails | Log, mark region failed, continue to next region |
| Login timeout (5 min) | Skip region, log warning |

## Logging
Per-region summary files:
```
logs/summary_US_<timestamp>.txt
logs/summary_CA_<timestamp>.txt
logs/summary_UK_<timestamp>.txt
logs/summary_EU_<timestamp>.txt
```
Combined cross-region summary printed to terminal at end.

## FC Code Prefixes

### Canada
YVR, YYZ, PRTO, YYC, YOW, YEG, YUL

### UK
BHX, CWL, EDI, EUK, GLA, LBA, LCY, LTN, MAN, XUK

### EU (→ amazon.de)
- Germany: BER, CGN, DTM, DUS, EDE, FRA, HAM, LEJ, MUC, STR, XDE
- France: CDG, LYS, ORY, MRS, NTE, BOD, TLS
- Spain: BCN, MAD, SVQ, XES
- Italy: MXP, FCO, TRN, XIT
- Poland: KTW, POZ, SZZ, WRO, LCJ
- Czech: PRG

## Changes to Existing Modules

### parse_excel.py
- Add `parse_and_filter_by_region(config)` — returns dict keyed by region name
- `load_fc_prefixes(file)` replaces `load_us_fc_prefixes()` (generic, reusable)
- Keep `parse_and_filter()` for backward compatibility with `--from-json` flow

### run.py
- Add `--regions` flag
- Replace single-region flow with region loop
- Per-region login check before upload
- Per-region summary written to logs
- Combined summary at end

### config.json / config.json.example
- Add `regions` array
- Keep `amazon_base_url` for backward compatibility

### upload_tracking.py
- No changes needed — `base_url` is already a parameter everywhere

### New files
- `fc_codes/` directory with 4 region files
