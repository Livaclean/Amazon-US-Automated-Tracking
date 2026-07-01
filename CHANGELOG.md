# Changelog

All notable changes to this project will be documented in this file.
Format based on [Keep a Changelog](https://keepachangelog.com/).

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
