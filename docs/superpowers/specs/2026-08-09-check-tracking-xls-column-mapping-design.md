# Fix .xls Column Mapping in --check-tracking's Row Context
**Date:** 2026-08-09
**Status:** Approved

## Overview

`--check-tracking` (`tracking_status.py`, shipped 2026-08-03 but never live-verified) crashed on its first real run against the live input sheet. `load_row_context()` hardcodes `openpyxl.load_workbook()`, but the real input file is the legacy `.xls` format, which openpyxl cannot read at all.

Fixing just the crash isn't enough. The real input file has 4 sheets (US, DE, AU, FR) with inconsistent column layouts — the US sheet has a separate "ITEMS" column that DE/AU/FR don't, shifting every column after it by one, and AU/FR put their header on row 0 instead of row 1. `parse_excel.py` already solves this class of problem for the FBA ID/TRACKING/DESTINATION/CARRIER columns via `detect_excel_engine()` + `_detect_xls_sheet_cols()` (per-sheet header auto-detection instead of trusting fixed config indices). `load_row_context()` never adopted either mechanism — it always uses openpyxl and always trusts fixed config-index positions for name/destination/ctns/shipping_way/notes. On the DE/AU/FR sheets this would silently pull the wrong column into each field (or drop `notes` entirely), even once the crash is fixed.

This spec covers making `load_row_context()` correct for the real `.xls` file across all 4 sheets, by extending the existing shared per-sheet detection logic in `parse_excel.py` rather than inventing a parallel mechanism.

## Scope

**In scope:** the `.xls` path only. Every real input file observed is `.xls`; no `.xlsx` files exist in `input/` and there's no evidence the `.xlsx` path (fixed config-index columns, unchanged since this feature shipped) has ever hit a multi-layout problem.

**Out of scope:** the `.xlsx` path in `load_row_context()` stays exactly as-is. The existing fallback behavior for FBA ID/TRACKING/DESTINATION/CARRIER detection in `_detect_xls_sheet_cols()` is untouched — only the new NAME/CTNS/SHIPPING_WAY/NOTES detection follows the (new) warn-and-fallback pattern described below.

## Data Flow

1. `load_row_context(file_path, config)` dispatches on `parse_excel.detect_excel_engine(file_path)`:
   - `"openpyxl"` → today's unchanged logic (fixed config-index columns).
   - `"xlrd"` → new `_load_row_context_xls(file_path, config)`.
2. `_load_row_context_xls()` opens the workbook with `xlrd` and, per sheet, calls the extended `parse_excel._detect_xls_sheet_cols(sheet)` to get column positions for `col_fc` (destination), `col_name`, `col_ctns`, `col_shipping_way`, plus `col_notes` (see below), plus the header row index.
3. For each data row (`header_row + 1` .. `sheet.nrows - 1`), read cells at those column positions using `parse_excel._xlrd_cell_str` (already used elsewhere for consistent numeric-to-string handling), keyed by `row_number = r + 1` — the same convention `parse_excel.load_excel_file()`'s xlrd branch already uses, so context rows line up with the row numbers `build_check_list()` looks up.

## Column Detection

`_detect_xls_sheet_cols()` in `parse_excel.py` is extended from its current 5-field return (header row + FC/FBA/TRACKING/CARRIER) to also locate:

- **NAME** — header row cell containing "ORDER NO" (case-insensitive substring match). Covers both the US layout (separate "Order No" column) and the DE/AU/FR layout (merged "Order No-ITEMS" / "Order No ITEMS" column) — the merged variant is read as-is into `name`, which is correct given no separate columns exist in the source data.
- **CTNS** — header cell containing "CTNS" (matches "NO OF CTNS " on all 4 sheets despite whitespace variance).
- **SHIPPING_WAY** — header cell containing "SHIPPING" (matches "SHIPPING  WAY" on all 4 sheets).
- **NOTES** — *not* header-detected. Every real sheet carries the delivered-on free text in its last physical column (`sheet.ncols - 1`), regardless of that column's header text (blank on US/AU/FR, "ETAs" on DE). Always uses the last-column position.

Because the return now carries 9 fields instead of 5, `_detect_xls_sheet_cols()` returns a dict instead of a positional tuple. Its one existing caller, `load_excel_file()`, is updated to read from the dict; its behavior for FBA ID/TRACKING/DESTINATION/CARRIER is unchanged.

## Error Handling

- If the header row can't be found at all (no "FBA ID" + "TRACKING" match within the first 3 rows), the existing full-sheet fallback (`0, 3, 4, 7, 8` positions) applies unchanged — this path is not modified by this spec.
- If a header row *is* found but NAME, CTNS, or SHIPPING_WAY individually can't be located in it (header text doesn't contain the expected substring), that field falls back to its config-default column index (`column_name` / `column_ctns` / `column_shipping_way`, defaulting to 1/5/6) **and** logs a warning naming the sheet and the field, e.g. `Sheet 'DE': could not detect 'ctns' column from header, falling back to column 5`. This surfaces unexpected future layouts instead of silently misaligning data.
- NOTES has no fallback/warning case — it's always `ncols - 1`, which is defined for any non-empty sheet.

## Testing

- New `.xls`-format fixtures (built the same way existing xls tests in the repo construct them) reproducing the two real shapes:
  - An 11-column sheet with a separate ITEMS column and blank last-column header (like US) — asserts correct name/destination/ctns/shipping_way/notes extraction, including a notes value from the blank-header last column.
  - A 10-column sheet with a merged "Order No-ITEMS" column and a named "ETAs" last column (like DE) — asserts the same fields extract correctly despite the shifted layout and named notes header.
- A header-not-found case for NAME/CTNS/SHIPPING_WAY individually, asserting the config-default fallback value is used and a warning is logged.
- Existing `.xlsx` tests (`CONTEXT_CONFIG`, `_write_context_sheet`, `test_load_row_context_extracts_descriptive_columns`, `test_load_row_context_handles_blank_notes`) are left unchanged as regression coverage for the untouched openpyxl path.

## Verification

After implementation and unit tests pass, re-run `python run.py --check-tracking` live against the real input sheet (as attempted on 2026-08-09) and confirm: no crash, and `logs/tracking_status.xlsx` contains correct name/destination/ctns/shipping_way/notes for shipments across all 4 sheets (US, DE, AU, FR) — not just US.

## Changes to Existing Files

### `parse_excel.py`
- `_detect_xls_sheet_cols(sheet) -> dict` — extended to also detect `col_name`, `col_ctns`, `col_shipping_way`, `col_notes`; returns a dict instead of a tuple; logs a warning on per-field fallback for the three newly-detected fields.
- `load_excel_file()` — updated to unpack the dict instead of the old positional tuple. No behavior change.

### `tracking_status.py`
- `load_row_context()` — gains the `detect_excel_engine()` dispatch.
- New `_load_row_context_xls()` — xlrd-based, per-sheet, using the extended `_detect_xls_sheet_cols()` and `parse_excel._xlrd_cell_str()`.
- Existing openpyxl logic in `load_row_context()` is preserved as the `.xlsx` branch, unchanged.

### No changes
- `upload_tracking.py`, `fetch_sub_tracking.py`, `verify_tracking.py`, `highlight_excel.py`, `fc_resolver.py`, `run.py` — not touched by this fix.
