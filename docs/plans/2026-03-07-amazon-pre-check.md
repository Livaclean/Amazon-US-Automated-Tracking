# Amazon Pre-Check Before Carrier Scrape Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Before scraping carrier websites, check Amazon Seller Central for each FBA shipment to see if tracking is already uploaded — and skip carrier scraping for shipments that are already complete.

**Architecture:** Add `check_amazon_tracking_status()` to `upload_tracking.py` that navigates to each FBA tracking page and reads input values. Add `check_all_shipments_on_amazon()` that loops all shipments and categorizes them. Wire into `run.py` so Amazon pre-check runs after Excel parsing but before carrier scraping — only shipments still pending on Amazon proceed to the carrier scrape step.

**Tech Stack:** Playwright (existing), existing `navigate_to_shipment` + `_get_tracking_frame` helpers.

---

## New Flow

```
Excel parse → categorize_shipments (Excel)
           → write_shipment_records
           → Login to Amazon
           → check_all_shipments_on_amazon   ← NEW
               → already_complete: skip entirely
               → needs_upload: proceed
           → carrier scrape (only needs_upload)
           → Amazon upload
           → highlight output Excel
```

---

### Task 1: Add check_amazon_tracking_status() to upload_tracking.py

**Files:**
- Modify: `upload_tracking.py`

**What to add:**

A function that navigates to a shipment's tracking page, finds the iframe, reads all input values, and returns the Amazon-side status.

```python
def check_amazon_tracking_status(page, fba_id: str, config: dict) -> str:
    """
    Checks whether tracking is already uploaded on Amazon for a shipment.
    Navigates to the FBA tracking page, finds the iframe, reads input values.

    Returns:
      "complete"   — all tracking inputs are filled (nothing to do)
      "partial"    — some inputs filled, some empty
      "empty"      — no inputs filled (needs upload)
      "not_found"  — shipment page not found or iframe missing
    """
    base_url = config.get("amazon_base_url", "https://sellercentral.amazon.com")
    logs_folder = config.get("logs_folder", "logs")

    if not navigate_to_shipment(page, fba_id, base_url):
        return "not_found"

    # Wait for tracking iframe
    tracking_frame = None
    for _ in range(20):  # up to 10 seconds
        tracking_frame = _get_tracking_frame(page)
        if tracking_frame:
            break
        page.wait_for_timeout(500)

    if not tracking_frame:
        logger.warning(f"  [pre-check] No tracking iframe found for {fba_id}")
        return "not_found"

    # Wait for inputs to render
    try:
        tracking_frame.wait_for_selector("input[placeholder*='auto fill']", timeout=10000)
    except Exception:
        logger.warning(f"  [pre-check] Timed out waiting for inputs for {fba_id}")

    try:
        inputs = tracking_frame.query_selector_all("input[placeholder*='auto fill']")
    except Exception as e:
        logger.warning(f"  [pre-check] Could not query inputs for {fba_id}: {e}")
        return "not_found"

    if not inputs:
        logger.info(f"  [pre-check] No input boxes found for {fba_id}")
        return "not_found"

    filled_count = 0
    for inp in inputs:
        try:
            val = inp.get_attribute("value") or inp.evaluate("e => e.value") or ""
            if val.strip():
                filled_count += 1
        except Exception:
            pass

    total = len(inputs)
    if filled_count == 0:
        return "empty"
    elif filled_count == total:
        return "complete"
    else:
        return "partial"
```

No tests needed for this function (requires a live browser). Verify manually in Task 3.

---

### Task 2: Add check_all_shipments_on_amazon() to upload_tracking.py

**Files:**
- Modify: `upload_tracking.py`

**What to add:**

A function that loops all FBA shipments from the Excel (those with tracking), checks each on Amazon, and returns two groups.

```python
def check_all_shipments_on_amazon(shipments_raw: dict, config: dict, page) -> tuple:
    """
    For each FBA shipment that has tracking in Excel, checks Amazon to see if
    tracking is already fully uploaded.

    Returns: (needs_upload, already_complete)
      - needs_upload: dict of {fba_id: entries} — empty or partial on Amazon
      - already_complete: list of fba_ids — all tracking already on Amazon
    """
    needs_upload = {}
    already_complete = []

    total = len(shipments_raw)
    for i, (fba_id, entries) in enumerate(shipments_raw.items(), 1):
        logger.info(f"  [{i}/{total}] Checking Amazon: {fba_id}")
        status = check_amazon_tracking_status(page, fba_id, config)
        logger.info(f"  -> Amazon status: {status}")

        if status == "complete":
            already_complete.append(fba_id)
            print(f"  [DONE]    {fba_id} — already complete on Amazon")
        else:
            needs_upload[fba_id] = entries
            label = "partial" if status == "partial" else ("not found" if status == "not_found" else "pending")
            print(f"  [PENDING] {fba_id} — {label}")

    return needs_upload, already_complete
```

No tests needed (requires live browser). Verify manually in Task 3.

---

### Task 3: Wire pre-check into run.py

**Files:**
- Modify: `run.py`

**What changes:**

1. Import `check_all_shipments_on_amazon` in the deferred imports block inside `main()`:

```python
from upload_tracking import (
    create_browser_context,
    check_login_status,
    discover_page_elements,
    upload_all_shipments,
    check_all_shipments_on_amazon,   # NEW
)
```

2. After `write_shipment_records` and before the carrier scrape block, add the Amazon pre-check step. The current Step 3 block starts with `if args.skip_carrier:`. Insert the pre-check BEFORE that block, but AFTER the browser is launched and login is checked.

**Current flow in main():**
```
Step 2: Launch browser
...
check_login_status(page, ...)  ← currently only called before upload (step 4)
...
Step 3: if args.skip_carrier → use main tracking directly
         else → get_all_sub_tracking
...
Step 4: check_login_status + upload_all_shipments
```

**New flow:**
```
Step 2: Launch browser
Step 3: Login + Amazon pre-check (NEW)
Step 4: Carrier scrape (only for needs_upload)
Step 5: Upload
```

Replace the current Step 3 + Step 4 block with:

```python
        # Step 3: Login to Amazon and pre-check which shipments need tracking
        print("\n[1/3] Checking Amazon for shipments that already have tracking...")
        check_login_status(page, config["amazon_base_url"])
        shipments_to_process, already_complete = check_all_shipments_on_amazon(
            shipments_raw, config, page
        )

        # Write already-complete list to logs
        if already_complete:
            ts_ac = datetime.now().strftime("%Y%m%d_%H%M%S")
            ac_file = Path(config["logs_folder"]) / f"shipments_already_complete_{ts_ac}.txt"
            ac_file.write_text("\n".join(sorted(already_complete)), encoding="utf-8")
            logger.info(f"Already-complete shipments saved to: {ac_file}")
            print(f"\n  {len(already_complete)} already complete on Amazon — skipped.")
            print(f"  {len(shipments_to_process)} still need tracking.")

        if not shipments_to_process:
            print("\nAll shipments already have tracking on Amazon. Nothing to upload.")
            # Still move/highlight the output file
            # Fall through to the file-processing step below with empty results
            results = []
        else:
            # Step 4: Fetch sub-tracking IDs from carrier sites (or use main tracking directly)
            if args.skip_carrier:
                print("\n[2/3] Skipping carrier scraping — using main tracking numbers directly...")
                shipments_with_subs = {}
                for fba_id, entries in shipments_to_process.items():
                    main_ids = [e["tracking"] for e in entries if e.get("tracking")]
                    logger.info(f"FBA {fba_id}: using {len(main_ids)} main tracking number(s) directly")
                    shipments_with_subs[fba_id] = main_ids
            else:
                print("\n[2/3] Fetching sub-package tracking IDs from UPS/FedEx...")
                shipments_with_subs = {}
                for fba_id, entries in shipments_to_process.items():
                    logger.info(f"\nFBA {fba_id}: {len(entries)} main tracking entries")
                    sub_ids = get_all_sub_tracking(page, entries, config["logs_folder"])
                    logger.info(f"  -> {len(sub_ids)} sub-tracking IDs collected")
                    shipments_with_subs[fba_id] = sub_ids

            # Save tracking IDs to file
            ts_ids = datetime.now().strftime("%Y%m%d_%H%M%S")
            tracking_ids_file = Path(config["logs_folder"]) / f"tracking_ids_{ts_ids}.json"
            combined = {
                fba_id: {
                    "parent": shipments_to_process.get(fba_id, []),
                    "sub_ids": shipments_with_subs.get(fba_id, []),
                }
                for fba_id in set(list(shipments_to_process.keys()) + list(shipments_with_subs.keys()))
            }
            with open(tracking_ids_file, "w", encoding="utf-8") as f:
                json.dump(combined, f, indent=2)
            logger.info(f"Tracking IDs saved to: {tracking_ids_file}")
            print(f"\nTracking IDs saved to: {tracking_ids_file}")

            # Step 5: Upload to Amazon Seller Central
            print("\n[3/3] Uploading to Amazon Seller Central...")
            results = upload_all_shipments(shipments_with_subs, config, page)
```

Also remove the now-redundant `check_login_status` call that used to be in Step 4 (it moved to Step 3 above).

**Verification:** Run `.venv/Scripts/python.exe run.py --skip-carrier` and confirm:
- Browser opens, logs into Amazon
- Each FBA shipment is visited and checked
- "Already complete" ones are printed as `[DONE]`
- Pending ones proceed to upload
- `logs/shipments_already_complete_<ts>.txt` is written

---

## Summary of Changed Files

| File | Change |
|---|---|
| `upload_tracking.py` | Add `check_amazon_tracking_status()` and `check_all_shipments_on_amazon()` |
| `run.py` | Wire pre-check into main flow; restructure Step 3/4/5 |
