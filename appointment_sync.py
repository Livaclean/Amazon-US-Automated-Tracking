# appointment_sync.py
"""
For TRUCK-carrier shipments that have no real tracking number yet, enters the
Appointment ID (already known from the freight forwarder's notes, e.g.
"Appointment ID: 83299056997   Delivered On:07/15") into Amazon's "Pro/Freight
Bill Number" field -- the field Amazon uses as this shipment's tracking
identifier once a truck delivery appointment has been scheduled.
"""
import logging
import re

logger = logging.getLogger(__name__)

# The colon after "Appointment ID" isn't consistent in the source data --
# confirmed live in the master sheet: plenty of real notes (both AWD and
# regular FBA TRUCK shipments, e.g. FBA1972Q93K1) read "Appointment ID
# 142628039989 ..." with no colon at all, which a colon-required pattern
# silently never matched. Making it optional catches both without any
# functional change for the colon-present rows.
_APPOINTMENT_ID_PATTERN = re.compile(r"Appointment ID:?\s*(\d+)")

# Once a Pro/Freight number is saved, Amazon replaces the empty editable input
# with a read-only "Pro/Freight: <value> (Edit)" summary line -- the input
# element (and its "Pro/Freight Bill Number:" label) no longer exist in the
# DOM at all. This matches only that saved-summary line, not the unsaved
# field's "Pro/Freight Bill Number:" label (no colon directly after "Freight").
_SAVED_PRO_FREIGHT_PATTERN = re.compile(r"Pro/Freight:\s*(\S+)")


def _extract_appointment_id_from_notes(notes: str) -> str:
    """Parses 'Appointment ID: 83299056997   Delivered On:07/15' -> '83299056997'. None if absent."""
    if not notes:
        return None
    match = _APPOINTMENT_ID_PATTERN.search(notes)
    return match.group(1) if match else None


def needs_appointment_sync(entry: dict) -> bool:
    """
    True if this master-sheet row is a TRUCK-carrier shipment with no real
    tracking number yet (blank, or the "/" placeholder used for not-yet-tracked
    shipments) and its notes carry an Appointment ID we can enter on Amazon.

    Excludes AWD shipments (FBA ID starts with "STAR-"): live testing showed
    their shipment page has no "Pro/Freight Bill Number" field at all -- AWD
    is a different program with different tracking mechanics, not just a
    different URL for the same field.
    """
    carrier = str(entry.get("carrier", "")).strip().upper()
    if carrier != "TRUCK":
        return False
    fba_id = str(entry.get("fba_id", "")).strip()
    if fba_id.startswith("STAR-"):
        return False
    tracking = str(entry.get("tracking", "")).strip()
    if tracking and tracking != "/":
        return False
    return _extract_appointment_id_from_notes(entry.get("notes", "")) is not None


def fill_pro_freight_number(page, fba_id: str, base_url: str, appointment_id: str) -> dict:
    """
    Navigates to fba_id's shipment page and fills+saves the Pro/Freight Bill
    Number field with appointment_id. Never overwrites an existing value.
    Returns {"status": ..., "value": ...} where status is one of:
    "filled" (saved successfully; value == appointment_id), "already_set" (a
    value was already there and left untouched; value == what Amazon actually
    has, which may differ from appointment_id), "no_field" (page loaded but
    the field wasn't found; value None), "nav_failed" (shipment page didn't
    load; value None).
    """
    from upload_tracking import navigate_to_shipment, _get_tracking_context

    if not navigate_to_shipment(page, fba_id, base_url):
        return {"status": "nav_failed", "value": None}

    ctx = _get_tracking_context(page, fba_id)
    if ctx is None:
        return {"status": "no_field", "value": None}

    try:
        # Present in both the unsaved ("Pro/Freight Bill Number:") and saved
        # ("Pro/Freight: <value>") states -- a reliable readiness signal
        # either way, since the two states render on different timelines.
        ctx.wait_for_selector("text=Pro/Freight", timeout=15000)
    except Exception:
        logger.warning(f"  {fba_id}: 'Pro/Freight' section never rendered")
        return {"status": "no_field", "value": None}

    saved = _SAVED_PRO_FREIGHT_PATTERN.search(ctx.inner_text("body"))
    if saved:
        logger.info(f"  {fba_id}: Pro/Freight already set to {saved.group(1)!r} -- not overwriting")
        return {"status": "already_set", "value": saved.group(1)}

    field = ctx.locator("kat-input.pro-freight-input").locator("input")
    if field.count() == 0:
        return {"status": "no_field", "value": None}

    field.first.fill(appointment_id)
    save_btn = ctx.get_by_text("Save", exact=True)
    if save_btn.count() == 0:
        return {"status": "no_field", "value": None}
    save_btn.first.click()
    page.wait_for_timeout(2000)
    return {"status": "filled", "value": appointment_id}


def _process_region_appointment_sync(page, base_url: str, items: list, sheet: dict) -> dict:
    """
    Fills the Pro/Freight number for `items` (list of (fba_id, appointment_id)
    tuples, all in the same region). Both a fresh fill and an already-set
    shipment record Amazon's confirmed value as this shipment's tracking
    number and mark tracking_status "updated" -- matching what "updated"
    already means elsewhere (a tracking identifier was successfully recorded,
    whether by us just now or already present). Only a genuine failure leaves
    the sheet untouched, so it's retried on the next run.
    Returns {"filled", "already_set", "failed"} counts.
    """
    counts = {"filled": 0, "already_set": 0, "failed": 0}
    for fba_id, appointment_id in items:
        result = fill_pro_freight_number(page, fba_id, base_url, appointment_id)
        status = result["status"]
        if status in ("filled", "already_set"):
            counts[status] += 1
            sheet[fba_id]["tracking"] = result["value"]
            sheet[fba_id]["tracking_status"] = "updated"
        else:
            counts["failed"] += 1
    return counts


def run_appointment_sync(config: dict) -> dict:
    """
    For every master-sheet shipment that needs_appointment_sync(), enters its
    Appointment ID into Amazon's Pro/Freight Bill Number field, one region at
    a time (its own browser login). Saves the master sheet after each region.
    Returns {"filled", "already_set", "failed"}.
    """
    from master_sheet import load_master_sheet, save_master_sheet, MASTER_SHEET_PATH_DEFAULT
    from upload_tracking import create_browser_context
    from run import wait_for_login

    path = config.get("master_sheet_path", MASTER_SHEET_PATH_DEFAULT)
    sheet = load_master_sheet(path)
    region_by_name = {r["name"]: r for r in config.get("regions", [])}

    pending_by_region = {}
    for fba_id, entry in sheet.items():
        if not needs_appointment_sync(entry):
            continue
        appointment_id = _extract_appointment_id_from_notes(entry.get("notes", ""))
        pending_by_region.setdefault(entry.get("region"), []).append((fba_id, appointment_id))

    totals = {"filled": 0, "already_set": 0, "failed": 0}
    if not pending_by_region:
        return totals

    playwright, context = create_browser_context(config)
    try:
        page = context.new_page()
        for region_name, items in pending_by_region.items():
            region = region_by_name.get(region_name)
            if not region:
                logger.warning(f"No config entry for region {region_name!r} -- skipping {len(items)} shipment(s)")
                totals["failed"] += len(items)
                continue

            base_url = region["amazon_url"]
            if not wait_for_login(page, region_name, base_url):
                logger.warning(f"Could not log in to {region_name} -- skipping {len(items)} shipment(s)")
                totals["failed"] += len(items)
                continue

            region_totals = _process_region_appointment_sync(page, base_url, items, sheet)
            for key in totals:
                totals[key] += region_totals[key]
            save_master_sheet(path, sheet)
    finally:
        try:
            context.close()
            playwright.stop()
        except Exception:
            pass

    return totals


def format_appointment_sync_summary(result: dict) -> str:
    lines = [
        "=" * 60,
        "APPOINTMENT ID SYNC SUMMARY",
        "=" * 60,
        f"Filled:                   {result['filled']}",
        f"Already set (skipped):    {result['already_set']}",
        f"Failed:                   {result['failed']}",
        "=" * 60,
    ]
    return "\n".join(lines)
