# Post-Upload Verification: Missing Tracking ID Check
**Date:** 2026-06-01
**Status:** Approved

## Overview

After every upload run, the tool navigates Amazon's shipping queue for each configured region, filters by "Ready to ship", paginates through all results, and finds any FBA with a "Missing Tracking ID" badge. It cross-references those FBAs against the sheet, re-uploads with a full carrier scrape where possible, and reports outcomes in the final summary. A standalone `--verify` flag allows running this check without uploading.

## Command Structure

```
python run.py                        # full pipeline: carrier scrape + upload + auto-verify (unchanged default)
python run.py --verify               # standalone queue check only, no upload
python run.py --regions US CA        # upload + verify for specific regions (unchanged)
python run.py --verify --regions EU  # standalone verify for EU only
```

No breaking changes. All existing flags (`--skip-carrier`, `--only-fba`, `--from-json`, `--collect-only`, `--check-only`, `--discover`, `--rewrite`) are unaffected. `--verify` can be combined with `--regions`.

## New Module: `verify_tracking.py`

Single entry point:

```python
def run_verify(page, region: dict, config: dict, shipments_all: dict) -> VerifyResult
```

### VerifyResult fields

| Field | Type | Description |
|-------|------|-------------|
| `region` | str | Region name (e.g. "US") |
| `total_checked` | int | Total "Ready to ship" shipments found on queue |
| `total_ok` | int | Shipments with no missing tracking badge |
| `re_uploaded` | list[dict] | FBAs successfully re-uploaded — `{fba_id, slots_filled}` |
| `still_incomplete` | list[dict] | FBAs re-uploaded but slots filled < total slots — `{fba_id, filled, total}` |
| `missing_in_sheet` | list[dict] | In sheet but tracking blank or "/" — `{fba_id, reason}` |
| `not_in_sheet` | list[str] | FBA ID not found in sheet at all |

### Step-by-step flow

1. Navigate to `{amazon_url}/gp/ssof/shipping-queue.html#fbashipment`
2. Wait for page load; handle login redirect via existing `_wait_for_login()`
3. Click Status filter → select "Ready to ship" → click Apply
4. Wait for filtered results to load
5. **Paginate through all pages** — collect every FBA ID that has a "Missing Tracking ID" badge; click Next until no Next button exists
6. Cross-reference collected FBA IDs against `shipments_all`:
   - FBA in sheet with valid tracking → **re-upload bucket**
   - FBA in sheet but tracking blank or "/" → **missing-in-sheet bucket**
   - FBA not in sheet → **not-in-sheet bucket**
7. For each FBA in re-upload bucket: run full carrier scrape (`get_all_sub_tracking`) then upload (`upload_tracking_to_shipment`)
8. After re-upload: check slot count — if filled < total, move to **still-incomplete bucket**
9. Update done cache: FBAs fully filled → add to done cache; still-incomplete → leave out
10. Return `VerifyResult`

## Pagination

Pagination is a hard requirement — the queue may span many pages. The verifier must:
- After each page, collect all "Missing Tracking ID" FBA IDs from that page
- Check for a "Next" / next-page button
- Click it and wait for the next page to load
- Repeat until no next-page button is found

No FBA may be skipped due to pagination stopping early.

## Cross-Reference Logic

"Usable tracking" means the tracking column has a non-empty value that is not "/" (and not whitespace-only). This mirrors the existing `categorize_shipments()` logic in `parse_excel.py`.

| Condition | Bucket |
|-----------|--------|
| FBA ID not in `shipments_all` | not-in-sheet |
| FBA ID in sheet, tracking blank or "/" | missing-in-sheet |
| FBA ID in sheet, valid tracking present | re-upload |

## Re-Upload Flow

For FBAs in the re-upload bucket, reuse existing pipeline functions exactly:
1. `get_all_sub_tracking(page, entries, logs_folder)` — full carrier scrape (UPS/FedEx)
2. `upload_tracking_to_shipment(page, sub_ids, fba_id, config)` — upload to Amazon
3. After upload, call `get_slot_count(page, fba_id, base_url)` to confirm how many slots are filled

This is identical to the main upload flow — no new upload logic needed.

## Done Cache

| Outcome | Done cache action |
|---------|------------------|
| All slots filled after re-upload | Add to done cache |
| Still incomplete after re-upload | Leave out of done cache (retry next run) |
| Missing in sheet | Leave out of done cache |
| Not in sheet | Leave out of done cache |

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Queue page fails to load | Log warning, skip verification for that region, note in summary |
| Status filter selector not found | Log warning + screenshot, skip region |
| No "Missing Tracking ID" badges found | Print "All tracking complete" for region |
| Re-upload fails for an FBA | Mark still-incomplete, include in summary |
| Login expires mid-verify | Reuse existing `_wait_for_login()` |
| Amazon UI change breaks selectors | Run `python run.py --discover` to re-identify selectors |

## Summary Output Format

Appended after the existing upload summary, one block per region:

```
============================================================
VERIFICATION — Missing Tracking ID Check
============================================================
Region: US
  Checked : 42 "Ready to ship" shipments
  OK       : 38 (tracking complete)
  Missing  : 4

  Re-uploaded successfully:
    FBA12345678  — 3 tracking IDs filled
    FBA87654321  — 5 tracking IDs filled

  Still incomplete after re-upload:
    FBA11111111  — 2 of 4 slots filled (fewer tracking IDs than fields)

  Tracking missing in sheet (in sheet but no usable tracking ID):
    FBA22222222  — tracking column blank
    FBA33333333  — tracking column is "/"

  Not in sheet (FBA ID not found in sheet at all):
    FBA99999999
    FBA88888888
============================================================
```

Followed by a combined cross-region totals line at the bottom of the full run output.

## Changes to Existing Files

### `verify_tracking.py` (new)
- `run_verify(page, region, config, shipments_all) -> VerifyResult`
- All queue navigation, pagination, cross-reference, and re-upload logic

### `run.py`
- Add `--verify` argument to argparse
- After the main upload loop: call `run_verify` for each region, collect `VerifyResult` list
- In `--verify` standalone mode: skip upload loop entirely, run verify loop only
- Print verification summary section after existing upload summary

### No changes
- `upload_tracking.py` — reused as-is
- `fetch_sub_tracking.py` — reused as-is
- `parse_excel.py` — reused as-is
- `highlight_excel.py` — reused as-is
