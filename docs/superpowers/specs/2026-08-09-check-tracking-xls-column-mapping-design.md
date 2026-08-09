# Fix .xls Column Mapping in --check-tracking's Row Context
**Date:** 2026-08-09
**Status:** Approved

## Overview

`--check-tracking` (`tracking_status.py`, shipped 2026-08-03 but never live-verified) crashed on its first real run against the live input sheet. `load_row_context()` hardcodes `openpyxl.load_workbook()`, but the real input file is the legacy `.xls` format, which openpyxl cannot read at all.

Fixing just the crash isn't enough. The real input file has 4 sheets (US, DE, AU, FR) with inconsistent column layouts — the US sheet has a separate "ITEMS" column that DE/AU/FR don't, shifting every column after it by one, and AU/FR put their header on row 0 instead of row 1. `parse_excel.py` already solves this class of problem for the FBA ID/TRACKING/DESTINATION/CARRIER columns via `detect_excel_engine()` + `_detect_xls_sheet_cols()` (per-sheet header auto-detection instead of trusting fixed config indices). `load_row_context()` never adopted either mechanism — it always uses openpyxl and always trusts fixed config-index positions for name/destination/ctns/shipping_way/notes. On the DE/AU/FR sheets this would silently pull the wrong column into each field (or drop `notes` entirely), even once the crash is fixed.

This spec covers making `load_row_context()` correct for the real `.xls` file across all 4 sheets, by extending the existing shared per-sheet detection logic in `parse_excel.py` rather than inventing a parallel mechanism.

While drafting the implementation plan, tracing the row-number join that `build_check_list()` uses to attach this context to each shipment surfaced a second, more serious problem (see **Context Join Key** below): that join was never sheet-scoped, so once the `.xls` crash is fixed it would silently attribute the wrong shipment's name/destination/ctns/shipping_way/notes across sheets. This spec now also covers that fix.

## Scope

**In scope:** the `.xls` path for column detection. Every real input file observed is `.xls`; no `.xlsx` files exist in `input/` and there's no evidence the `.xlsx` path (fixed config-index columns, unchanged since this feature shipped) has ever hit a multi-layout problem. The context join-key fix (FBA-ID-keyed instead of row-number-keyed) applies to **both** readers, since `build_check_list()` uses one unified lookup — see below.

**Out of scope:** column *detection* for `.xlsx` stays exactly as-is (fixed config-index positions). The existing fallback behavior for FBA ID/TRACKING/DESTINATION/CARRIER detection in `_detect_xls_sheet_cols()` is untouched — only the new NAME/CTNS/SHIPPING_WAY/NOTES detection follows the (new) warn-and-fallback pattern described below. No changes to `parse_excel.py`'s shared row-entry shapes (`load_excel_file()`, `group_by_fba_id()`, `parse_and_filter_by_region()`) or to anything in `run.py`/`highlight_excel.py` that depends on `row_number` for Excel-highlighting purposes — that field's existing meaning and usage elsewhere in the app is untouched.

## Data Flow

1. `load_row_context(file_path, config)` dispatches on `parse_excel.detect_excel_engine(file_path)`:
   - `"openpyxl"` → today's unchanged logic (fixed config-index columns).
   - `"xlrd"` → new `_load_row_context_xls(file_path, config)`.
2. `_load_row_context_xls()` opens the workbook with `xlrd` and, per sheet, calls the extended `parse_excel._detect_xls_sheet_cols(sheet)` to get column positions for `col_fc` (destination), `col_name`, `col_ctns`, `col_shipping_way`, plus `col_notes` (see below), plus the header row index.
3. For each data row (`header_row + 1` .. `sheet.nrows - 1`), read cells at those column positions using `parse_excel._xlrd_cell_str` (already used elsewhere for consistent numeric-to-string handling), keyed by FBA ID (see **Context Join Key** below) rather than row number.

## Column Detection

`_detect_xls_sheet_cols()` in `parse_excel.py` is extended from its current 5-field return (header row + FC/FBA/TRACKING/CARRIER) to also locate:

- **NAME** — header row cell containing "ORDER NO" (case-insensitive substring match). Covers both the US layout (separate "Order No" column) and the DE/AU/FR layout (merged "Order No-ITEMS" / "Order No ITEMS" column) — the merged variant is read as-is into `name`, which is correct given no separate columns exist in the source data.
- **CTNS** — header cell containing "CTNS" (matches "NO OF CTNS " on all 4 sheets despite whitespace variance).
- **SHIPPING_WAY** — header cell containing "SHIPPING" (matches "SHIPPING  WAY" on all 4 sheets).
- **NOTES** — *not* header-detected. Every real sheet carries the delivered-on free text in its last physical column (`sheet.ncols - 1`), regardless of that column's header text (blank on US/AU/FR, "ETAs" on DE). Always uses the last-column position.

Because the return now carries 9 fields instead of 5, `_detect_xls_sheet_cols()` returns a dict instead of a positional tuple. Its one existing caller, `load_excel_file()`, is updated to read from the dict; its behavior for FBA ID/TRACKING/DESTINATION/CARRIER is unchanged.

## Context Join Key: FBA ID Instead of Row Number

**The bug:** `load_excel_file()`'s xlrd branch numbers each sheet's data rows independently, starting at `row_number = 2` — it carries no sheet identity. `build_check_list()` joins each shipment to its descriptive context via that bare `row_number` in one flat dict spanning the whole file. The real input file's 4 sheets all start at row 2 and their ranges overlap heavily (US rows 2–556, DE 2–35, AU 2–12, FR row 2), so once `load_row_context()` stops crashing, `context[2]` would be written by US then overwritten by DE, then AU, then FR — last sheet processed wins. Most non-US shipments would display another region's name/destination/ctns/shipping_way/notes instead of their own. This bug already existed (the row-number scheme was never sheet-scoped); it was simply unreachable while `.xls` crashed before the join ran.

**Why not sheet-scope `row_number` itself:** that field is also used by `run.py`'s `collect_updated_row_numbers()` to physically address rows in the source file for post-upload highlighting (`highlight_excel.py`) — a live, shipped feature with no relation to `--check-tracking`. Redefining it, or adding a disambiguating field to the row-entry dicts `group_by_fba_id()` builds in `parse_excel.py`, would change a shape shared by `run.py`, `highlight_excel.py`, `fc_resolver.py`, and four other test files that assert exact dict equality on those entries — real scope creep into shared, already-shipped code.

**The fix:** key the context dict by **FBA ID** instead of row number. FBA IDs are globally unique across the whole file — they're Amazon's own shipment identifiers, not per-sheet positions — and `build_check_list()` already has each entry's `fba_id` in scope at the exact point it performs the context lookup (it's the loop variable from iterating `fba_shipments.items()`). This confines the fix entirely to `tracking_status.py`:

- `_row_context_from_xls_book()` reads each row's FBA ID via `_xlrd_cell_str(sheet, r, cols["col_fba"])` (the `_detect_xls_sheet_cols()` dict already includes `col_fba`) and keys the context dict by that value instead of `row_number`.
- `_load_row_context_xlsx()` (the `.xlsx` path) is updated the same way for consistency, since `build_check_list()` uses one unified lookup against a context dict that may be populated by either reader: it reads a new `col_fba = config.get("column_fba_id", 4)` (an existing, already-standard config key used elsewhere in the app) and keys by that cell's value instead of `idx + 2`.
- `build_check_list()`'s lookup changes from `context.get(entry.get("row_number"), {})` to `context.get(fba_id, {})`.

This changes `_load_row_context_xlsx()`'s return-key scheme even though its column-*detection* logic (fixed config indices) stays untouched — a narrower exception to the "xlsx path stays exactly as-is" scope statement above, needed because both readers must agree on one key scheme for `build_check_list()`'s single lookup to work. Its 2 existing tests (`test_load_row_context_extracts_descriptive_columns`, `test_load_row_context_handles_blank_notes`) are updated to assert against the FBA-ID key instead of row-number.

## Error Handling

- If the header row can't be found at all (no "FBA ID" + "TRACKING" match within the first 3 rows), the existing full-sheet fallback (`0, 3, 4, 7, 8` positions) applies unchanged — this path is not modified by this spec.
- If a header row *is* found but NAME, CTNS, or SHIPPING_WAY individually can't be located in it (header text doesn't contain the expected substring), that field falls back to its config-default column index (`column_name` / `column_ctns` / `column_shipping_way`, defaulting to 1/5/6) **and** logs a warning naming the sheet and the field, e.g. `Sheet 'DE': could not detect 'ctns' column from header, falling back to column 5`. This surfaces unexpected future layouts instead of silently misaligning data.
- NOTES has no fallback/warning case — it's always `ncols - 1`, which is defined for any non-empty sheet.

## Testing

- New `.xls`-format fixtures (lightweight fake xlrd Sheet/Book objects, matching the existing `FakePage`/`FakeElement` duck-typed test-double pattern already used in `tests/test_verify_tracking.py` — no `.xls`-writing library is added as a dependency) reproducing the two real shapes:
  - An 11-column sheet with a separate ITEMS column and blank last-column header (like US) — asserts correct name/destination/ctns/shipping_way/notes extraction, including a notes value from the blank-header last column, keyed by FBA ID.
  - A 10-column sheet with a merged "Order No-ITEMS" column and a named "ETAs" last column (like DE) — asserts the same fields extract correctly despite the shifted layout and named notes header.
  - Two sheets whose row_numbers would collide (e.g. both have a row 2) but whose FBA IDs differ — asserts both are present in the resulting context under their own FBA ID, proving the join-key fix.
- A header-not-found case for NAME/CTNS/SHIPPING_WAY individually, asserting the config-default fallback value is used and a warning is logged.
- Existing `.xlsx` tests (`CONTEXT_CONFIG`, `_write_context_sheet`) updated to assert against the FBA-ID key instead of row-number; the rest of the openpyxl column-detection behavior is unchanged.

## Verification

After implementation and unit tests pass, re-run `python run.py --check-tracking` live against the real input sheet (as attempted on 2026-08-09) and confirm: no crash, and `logs/tracking_status.xlsx` contains correct name/destination/ctns/shipping_way/notes for shipments across all 4 sheets (US, DE, AU, FR) — not just US, and not cross-attributed between sheets.

## Changes to Existing Files

### `parse_excel.py`
- `_detect_xls_sheet_cols(sheet) -> dict` — extended to also detect `col_name`, `col_ctns`, `col_shipping_way`, `col_notes`; returns a dict instead of a tuple; logs a warning on per-field fallback for the three newly-detected fields.
- `load_excel_file()` — updated to unpack the dict instead of the old positional tuple. No behavior change.
- `group_by_fba_id()`, `parse_and_filter_by_region()`, and the `row_number` field's existing meaning/usage — untouched.

### `tracking_status.py`
- `load_row_context()` — gains the `detect_excel_engine()` dispatch.
- New `_load_row_context_xls()` / `_row_context_from_xls_book()` — xlrd-based, per-sheet, using the extended `_detect_xls_sheet_cols()` and `parse_excel._xlrd_cell_str()`; context keyed by FBA ID.
- `_load_row_context_xlsx()` (renamed from the current `load_row_context()` body) — column detection unchanged, but now also reads `column_fba_id` and keys its returned context by FBA ID instead of row_number, to match the xls reader's key scheme.
- `build_check_list()` — context lookup changes from `context.get(entry.get("row_number"), {})` to `context.get(fba_id, {})`.

### No changes
- `upload_tracking.py`, `fetch_sub_tracking.py`, `verify_tracking.py`, `highlight_excel.py`, `fc_resolver.py`, `run.py` — not touched by this fix.
