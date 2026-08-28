# Changelog

All notable changes to this project will be documented in this file.
Format based on [Keep a Changelog](https://keepachangelog.com/).

## [0.8.1] - 2026-08-29

### Fixed
- Unsupported/unrecognized carrier tracking (e.g. `BASL`, `DPD`) now fills every empty Amazon tracking slot for a shipment by duplicating its known tracking number(s), instead of filling only one slot and leaving the rest blank — these carriers have no per-box sub-ID scrape, and one pallet-level tracking number legitimately covers every box. Applies both in the main upload pass (`upload_tracking_to_shipment`'s new `pad_to_fill`) and in `--verify`'s re-upload path, including when some slots were already filled by a prior partial run
- `highlight_excel.py`'s `.xls` → `.xlsx` conversion (used when saving the highlighted output archive) now copies every sheet of the source workbook instead of only the first — it previously silently dropped every row on sheet 2+ even though `parse_excel.py`'s actual row-matching reads all sheets, so any multi-sheet source file's later-sheet rows vanished from the saved/processed output. Highlighting now applies to whichever sheet(s) contain a given updated row, since per-sheet row numbers aren't globally unique across sheets

## [0.8.0] - 2026-08-19

### Added
- Carton-tracking from source column L: when a source `.xls` sheet carries a trailing "carton tracking" blob (`TRACKING-FBAID+seq`, e.g. `1ZK6B4420338604208-FBA19L9DHD1SU000001`), the pipeline now parses it and uses those ground-truth per-carton tracking IDs directly instead of scraping UPS/FedEx or splitting a flat sub-tracking pool by Amazon's slot count — in both the main upload flow and `--verify`'s re-upload path. Malformed/ambiguous blob lines fall back to the carrier scrape rather than being guessed. Shortfalls (fewer cartons matched than the sheet's own carton count) upload what's available and are reported in a new end-of-run summary section
- FC-code ignore list (`fc_codes/ignored_fc_codes.txt`): FC codes that probe as unresolvable in every configured region (3PL/TikTok warehouses, freeform notes, etc. — not real Amazon FCs) are now persisted and skipped on future runs instead of being re-probed against every region every time

### Fixed
- `navigate_to_shipment()` now recognizes Amazon's cross-marketplace redirect ("The shipment you're trying to open is for Canada...") as a rejection instead of reading it as a successful page load — this had silently caused `fc_resolver.probe_fc_codes()` to misattribute a Canada-only FC code (`XYY4`) to the US region a week earlier, permanently misrouting two real shipments (confirmed live via screenshot)
- `check_amazon_tracking_status()` no longer reports an ambiguous detection failure (couldn't locate the tracking iframe/inputs after a successful navigation) as `"not_found"` — that value was being treated identically to "genuinely complete" by `check_all_shipments_on_amazon()`, silently caching shipments as done when they'd never actually received tracking. Now returns a distinct `"check_failed"` status that routes back into the upload queue instead
- `_detect_xls_sheet_cols()`'s "notes = last physical column" heuristic no longer collides with a sheet's carton-tracking column when both are present (as on the current US sheet, which added a 12th trailing column) — the carton-tracking column is now detected by content, and notes falls back one column earlier when it would otherwise land on it

## [0.7.1] - 2026-08-12

### Fixed
- TikTok shipment IDs (prefixed `IBR`) are no longer treated as Amazon FBA shipments — they don't exist on Amazon and were wasting a pre-check/upload visit per region on every run; now skipped in `group_by_fba_id` and the FC-resolver's `_dedupe_fba_ids`, the same way Walmart IDs (suffixed `WFA`) already were
- Console output no longer crashes with `UnicodeEncodeError` when a log/print message contains a character outside Windows' default `cp1252` codepage (e.g. CJK text in a supplier sheet's product name) — `stdout`/`stderr` are now reconfigured to UTF-8 at startup

## [0.7.0] - 2026-08-10

### Added
- `--sync-delivery-windows`: for master-sheet shipments that aren't Delivered and have a known Workflow ID, compares Amazon's delivery window against the real carrier expected-delivery date (from `logs/tracking_status.xlsx`) and edits the window on Amazon when it doesn't match. If there's no expected date yet and the window is about to lock (within 7 days of its own start), pushes it 2 weeks out as a defensive stopgap rather than let it lock on a guess
- `--discover-workflows` now skips shipments already marked Delivered in either status column, since a Workflow ID only exists to support delivery-window sync, which itself skips Delivered shipments -- cut a live 448-shipment run down to 88 candidates
- Master sheet's `Tracking Status` and `Delivery Date Status` columns now recognize a third state, "Delivered" (superseding "pending"/"updated"), detected from source notes or the `tracking_status.xlsx` cache -- delivery-window sync skips these shipments entirely

### Fixed
- Delivery-window sync no longer crashes when Amazon shows its "Save time with Send to Amazon" onboarding tour modal on the workflow page -- the modal's overlay was intercepting every click behind it; now dismissed automatically if present
- A stale (already-past) cached expected-delivery date -- an overdue "In Transit" package -- no longer causes an unclickable calendar-day target; Amazon correctly disables past dates, so a strictly-past expected date is now treated the same as not having one at all
- EU/FR-region shipment pages render "Delivery window: 1 Jul 2026 - 14 Jul 2026" (day-month-year, no comma) instead of the US-style "Jul 1, 2026" -- both formats are now parsed
- Documented 5 previously-undocumented CLI flags in the README (`--check-tracking`, `--update-master-sheet`, `--discover-workflows`, `--sync-appointments`, `--sync-delivery-windows`)

## [0.6.0] - 2026-08-10

### Added
- `--update-master-sheet`: populates/refreshes `logs/shipment_tracking_master.xlsx`, a persistent workbook (one row per FBA ID, never recreated) consolidating tracking status, delivery-date status, and Workflow ID for every shipment. Also runs automatically as a step in any normal run, reusing the same Excel parse already done that run — no extra cost, non-fatal on failure
- `--discover-workflows`: for shipments missing a Send-to-Amazon Workflow ID, visits the shipment page and follows "Send to Amazon (view)" to find it. A workflow covering several sibling shipments (split across destinations) is recorded for all of them from a single visit instead of opening each one separately
- `--sync-appointments`: for TRUCK-carrier shipments with no real tracking number yet, enters the Appointment ID already known from the source sheet's notes into Amazon's Pro/Freight Bill Number field — confirmed live that Amazon treats it as the shipment's tracking identifier once saved. Never overwrites a value Amazon already has; if Amazon already has one (e.g. auto-filled via carrier integration), syncs the sheet to that real value instead of leaving it stale. Skips AWD shipments, which have no equivalent field

### Fixed
- Workflow-page sibling links (used by `--discover-workflows`) render a few seconds after the page's Workflow ID does — a fixed wait was landing in that gap and missing them; now waits for the actual content instead of a guessed delay

## [0.5.2] - 2026-08-10

### Fixed
- `--check-tracking` no longer crashes on the real `.xls` input file — `load_row_context()` hardcoded `openpyxl`, which can't read the legacy `.xls` format used by the actual shipment sheet
- `--check-tracking`'s per-sheet column detection (name/destination/ctns/shipping_way/notes) now auto-detects each `.xls` sheet's own header layout instead of trusting fixed config-index columns, since the real file's sheets don't share one layout (an extra "ITEMS" column on one sheet shifted every later column on the others)
- Fixed a row-context join bug found while fixing the above: shipment context was keyed by `row_number`, which isn't unique across a file's sheets — now keyed by `(fba_id, row_number)` so multiple tracking numbers under one FBA ID (or shipments from different sheets) never overwrite each other's notes/status. This matters because `notes` gates whether a tracking number's carrier check is skipped as already-delivered
- FedEx tracking checks now retry once via page reload before falling back to page-text scraping, recovering the label-created date that the fallback path can't see (intermittent API-interception timeouts were observed in live testing)

### Added
- `_detect_xls_sheet_cols()` (shared with the main upload pipeline) now also detects name/ctns/shipping_way/notes columns per sheet, extending its existing FBA ID/tracking/destination/carrier detection

## [0.5.1] - 2026-07-28

### Fixed
- AU post-upload/standalone verification now uses `run_verify_au()` (new `/amazonsell/shipments` page, same as US/CA and UK/EU/FR) instead of the legacy per-region queue-page path, which had started failing ("Filters button not found") because AU runs the same modern Seller Central UI as US
- Added `AU_REGIONS` constant and `run_verify_au()` in `verify_tracking.py`; `run.py` now routes AU through the unified new-page verify path in both standalone `--verify` mode and post-upload verification

## [0.5.0] - 2026-07-18

### Added
- `--with-names` CLI flag: with `--verify`, pairs each reported FBA ID with its Amazon shipment name (US/CA and UK/EU/FR only) by walking the `/amazonsell/shipments` table rows and matching each row's FBA ID to its shipment-name link text
- `_extract_row_name_pairs()` in `verify_tracking.py` — row-level DOM extraction used by `--with-names`
- `VerifyResult.shipment_names` — dict of FBA ID → shipment name, populated only when `--with-names` is passed
- `_reupload_fba()` now returns the actual `tracking_ids` used, so `format_verify_summary()` can print them for re-uploaded and still-incomplete entries

### Changed
- `_collect_from_new_shipments_page()` now returns `(fba_ids, shipment_names)` instead of a flat list; `shipment_names` is empty unless `with_names=True`

## [0.4.0] - 2026-07-13

### Added
- `run_verify_eu()` and `EU_REGIONS` constant in `verify_tracking.py` — runs UK, EU, and FR verification once via the new `/amazonsell/shipments` page (unified Europe account), matching Amazon's migration of these marketplaces to the same page format already used for US/CA
- `_run_verify_unified()` — shared implementation behind `run_verify_na()` and `run_verify_eu()`, parameterized by an anchor region for the base URL

### Changed
- Post-upload verification and standalone `--verify` mode now call `run_verify_eu()` once for UK+EU+FR combined instead of the old per-region shipping-queue-page approach, which had started failing ("Filters button not found") now that Amazon serves the new page for these marketplaces too

## [0.3.0] - 2026-07-01

### Added
- DHL sub-tracking support in `fetch_sub_tracking.py` — `fetch_dhl_sub_tracking()` intercepts the `dhl.com/utapi` JSON response on page load to extract `details.pieceIds` directly (no DOM clicking needed); falls back to clicking the "Piece IDs" accordion button, then full-page regex
- AU region support: `fc_codes/au_fc_codes.txt` (BWU2), uploads to sellercentral.amazon.com.au
- FR region support: `fc_codes/fr_fc_codes.txt` (XCD2), uploads via sellercentral.amazon.de (same account as EU)
- `normalize_carrier()` now returns `'dhl'` for DHL carrier strings; `_detect_carrier_from_tracking()` detects JD-format DHL numbers

### Changed
- `fetch_sub_tracking_ids()` dispatches to `fetch_dhl_sub_tracking()` for DHL entries — previously logged a warning and returned empty list

## [0.2.0] - 2026-07-01

### Added
- `run_verify_na()` in `verify_tracking.py` — runs US and CA verification once via the new `/amazonsell/shipments` page (unified North America account), replacing the old per-region queue page approach for both regions
- `_collect_from_new_shipments_page()` — paginates the new Amazon shipments page via URL `pageIndex` parameter and extracts FBA IDs from page JSON data
- `NEW_SHIPMENTS_URL_TEMPLATE` and `NA_REGIONS` constants in `verify_tracking.py`

### Changed
- Post-upload verification and standalone `--verify` mode now call `run_verify_na()` once for US+CA combined instead of running two separate queue-page passes — avoids redundant login and scraping since both regions share a unified NA account
- UK, EU, and AWD regions continue using the existing shipping queue page (`_navigate_to_queue_page`)

## [0.1.2] - 2026-05-27

### Fixed
- Uploading to newly-added Amazon slots no longer duplicates already-uploaded IDs — script now reads which IDs are already in filled slots and only fills empty slots with genuinely missing IDs
- Multi-pass loop now tracks specifically-uploaded IDs (set subtraction) rather than a naive front-slice, so remaining IDs are correctly computed when empty slots appear at arbitrary positions
- `check_amazon_tracking_status` and `get_slot_count` now scroll the tracking iframe before querying inputs, ensuring dynamically-added slots are visible before counting

## [0.1.1] - 2026-05-27

### Fixed
- Blank carrier column in Excel no longer silently skips sub-tracking fetch — UPS tracking numbers (`1Z...` format) and FedEx numbers are now auto-detected from the tracking ID itself
- Amazon's 20-slot per-page cap no longer causes incomplete uploads — uploader now loops across multiple passes until all available slots are filled
- FedEx "Piece Shipment" detail section failing to load — extended wait from 10s to 25s and added explicit scroll + selector-based confirmation before reading sub-IDs

## [0.1.0] - 2026-05-12

### Added
- UPS sub-tracking now intercepts the `GetAdditionalPackages` JSON API response directly — no HTML parsing, no regex, immune to UI layout changes
- Condition-based waiting for UPS Angular app render (`wait_for_selector("button.custom-title-button")`) instead of flat timeout
- `_ups_click_drawer_button` and `_ups_extract_from_api` helpers to cleanly separate drawer interaction from data extraction
- `_wait_for_ups_drawer_content` for condition-based DOM fallback waiting
- `empty_slots_remaining` field in `upload_tracking_to_shipment` result — tracks how many Amazon slots couldn't be filled due to pool being smaller than slot count

### Changed
- UPS API pagination now handled via `page.expect_response` per page click, replacing DOM text slice + flat sleep approach
- `#stApp_pagination_nextBtn` added as first-priority Next button selector (UPS new layout specific)
- Done cache in `run.py` now excludes FBAs where `empty_slots_remaining > 0` — these re-enter the queue next run when UPS scans remaining packages
- DOM-based extraction retained as fallback behind API interception (reads `#stApp_multiPieceShipmentContent` directly, then full-page regex)

### Fixed
- UPS tracking page timeout on first load — Angular app was not fully rendered within old 4s wait
- FBAs with shared tracking pools and fewer IDs than Amazon slots were incorrectly marked as done in cache, preventing the missing slots from being filled on subsequent runs
