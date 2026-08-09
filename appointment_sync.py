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

_APPOINTMENT_ID_PATTERN = re.compile(r"Appointment ID:\s*(\d+)")


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
    """
    carrier = str(entry.get("carrier", "")).strip().upper()
    if carrier != "TRUCK":
        return False
    tracking = str(entry.get("tracking", "")).strip()
    if tracking and tracking != "/":
        return False
    return _extract_appointment_id_from_notes(entry.get("notes", "")) is not None


def fill_pro_freight_number(page, fba_id: str, base_url: str, appointment_id: str) -> str:
    """
    Navigates to fba_id's shipment page and fills+saves the Pro/Freight Bill
    Number field with appointment_id. Never overwrites an existing value.
    Returns one of: "filled" (saved successfully), "already_set" (a value was
    already there, left untouched), "no_field" (page loaded but the field
    wasn't found), "nav_failed" (shipment page didn't load).
    """
    from upload_tracking import navigate_to_shipment, _get_tracking_context

    if not navigate_to_shipment(page, fba_id, base_url):
        return "nav_failed"

    ctx = _get_tracking_context(page, fba_id)
    if ctx is None:
        return "no_field"

    field = ctx.locator("kat-input.pro-freight-input").locator("input")
    if field.count() == 0:
        return "no_field"

    existing = (field.first.input_value() or "").strip()
    if existing:
        logger.info(f"  {fba_id}: Pro/Freight already set to {existing!r} -- not overwriting")
        return "already_set"

    field.first.fill(appointment_id)
    save_btn = ctx.get_by_text("Save", exact=True)
    if save_btn.count() == 0:
        return "no_field"
    save_btn.first.click()
    page.wait_for_timeout(2000)
    return "filled"


def _process_region_appointment_sync(page, base_url: str, items: list, sheet: dict) -> dict:
    """
    Fills the Pro/Freight number for `items` (list of (fba_id, appointment_id)
    tuples, all in the same region). On a successful fill, records the
    appointment_id as this shipment's tracking number and marks
    tracking_status "updated" -- matching what "updated" already means
    elsewhere (a tracking identifier was successfully recorded with no error).
    Returns {"filled", "already_set", "failed"} counts.
    """
    counts = {"filled": 0, "already_set": 0, "failed": 0}
    for fba_id, appointment_id in items:
        result = fill_pro_freight_number(page, fba_id, base_url, appointment_id)
        if result == "filled":
            counts["filled"] += 1
            sheet[fba_id]["tracking"] = appointment_id
            sheet[fba_id]["tracking_status"] = "updated"
        elif result == "already_set":
            counts["already_set"] += 1
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
