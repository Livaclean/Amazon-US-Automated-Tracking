# shipment_status.py
"""
Populates the master sheet's "Shipment Status" column with Amazon's own
shipment lifecycle status (e.g. "Shipped", "Delivered"). A shipment already
marked Delivered by carrier tracking is stamped "Delivered" directly, with no
live Amazon visit -- there's nothing left to learn once it's actually
arrived. Only shipments still pending get a live check.
"""
import logging

logger = logging.getLogger(__name__)


def _backfill_delivered_shipment_status(sheet: dict) -> dict:
    """
    For every row already marked Delivered (tracking_status or
    delivery_date_status) whose amazon_shipment_status isn't already a more
    specific terminal Amazon status, sets amazon_shipment_status to
    "Delivered" directly -- no live Amazon check needed. A row already
    stamped "Closed" or "Receiving" by an earlier live check (workflow
    discovery or the weekly delivery-window sync) is left alone rather than
    downgraded to the generic "Delivered", since that would discard more
    specific information the column exists to report without ever having
    checked Amazon again. Rows not yet Delivered are left untouched. Returns
    a new dict; does not mutate sheet.
    """
    from master_sheet import is_carrier_delivered, is_terminal_shipment_status

    result = {fba_id: dict(entry) for fba_id, entry in sheet.items()}
    for entry in result.values():
        if is_carrier_delivered(entry) and not is_terminal_shipment_status(entry.get("amazon_shipment_status")):
            entry["amazon_shipment_status"] = "Delivered"
    return result


def _pending_fba_ids_by_region(sheet: dict) -> dict:
    """
    Groups FBA IDs needing a live Amazon shipment-status check by region:
    those not yet carrier-Delivered and not already stamped with a terminal
    Amazon status (Delivered/Closed/Receiving) by an earlier live check --
    re-visiting those live gives no new information, since none of them can
    change once Amazon considers them terminal.
    """
    from master_sheet import is_carrier_delivered, is_terminal_shipment_status

    pending_by_region = {}
    for fba_id, entry in sheet.items():
        if is_carrier_delivered(entry) or is_terminal_shipment_status(entry.get("amazon_shipment_status")):
            continue
        pending_by_region.setdefault(entry.get("region"), []).append(fba_id)
    return pending_by_region


def run_populate_shipment_status(config: dict) -> dict:
    """
    Populates every master-sheet row's Shipment Status column: rows already
    Delivered (by carrier tracking) are stamped "Delivered" with no browser
    visit; every other (pending) row gets a live check of Amazon's own
    shipment-status badge, one region at a time. Saves the master sheet
    after the backfill and after each region -- as a narrow per-row patch
    against a freshly reloaded copy, not a blind overwrite of this run's own
    in-memory snapshot, since workflow_discovery.py and
    delivery_window_sync.py each independently load/save the same file.
    Returns {"backfilled_delivered", "checked", "found", "not_found", "skipped"}.
    """
    from master_sheet import load_master_sheet, merge_field_updates, MASTER_SHEET_PATH_DEFAULT
    from upload_tracking import create_browser_context, navigate_to_shipment, fetch_shipment_status
    from run import wait_for_login, resolve_regions

    path = config.get("master_sheet_path", MASTER_SHEET_PATH_DEFAULT)
    sheet = load_master_sheet(path)

    before_delivered = sum(
        1 for e in sheet.values()
        if e.get("tracking_status") == "Delivered" or e.get("delivery_date_status") == "Delivered"
    )
    sheet = _backfill_delivered_shipment_status(sheet)
    backfilled_ids = [
        fba_id for fba_id, entry in sheet.items()
        if entry.get("amazon_shipment_status") == "Delivered"
    ]
    merge_field_updates(path, {fba_id: sheet[fba_id] for fba_id in backfilled_ids}, ["amazon_shipment_status"])

    totals = {"backfilled_delivered": before_delivered, "checked": 0, "found": 0, "not_found": 0, "skipped": 0}

    region_by_name = {r["name"]: r for r in resolve_regions(config)}
    pending_by_region = _pending_fba_ids_by_region(sheet)
    if not pending_by_region:
        return totals

    playwright, context = create_browser_context(config)
    try:
        page = context.new_page()
        for region_name, fba_ids in pending_by_region.items():
            region = region_by_name.get(region_name)
            if not region:
                logger.warning(f"No config entry for region {region_name!r} -- skipping {len(fba_ids)} shipment(s)")
                totals["skipped"] += len(fba_ids)
                continue
            base_url = region["amazon_url"]
            if not wait_for_login(page, region_name, base_url):
                logger.warning(f"Could not log in to {region_name} -- skipping {len(fba_ids)} shipment(s)")
                totals["skipped"] += len(fba_ids)
                continue

            touched_ids = []
            for fba_id in fba_ids:
                totals["checked"] += 1
                status = None
                if navigate_to_shipment(page, fba_id, base_url):
                    status = fetch_shipment_status(page)
                # A truthy check, not just "is not None": Amazon's status
                # badge can expose an empty label attribute before it's
                # finished hydrating client-side -- an empty string is never
                # a real status worth overwriting a previously-good value with.
                if status:
                    sheet[fba_id]["amazon_shipment_status"] = status
                    touched_ids.append(fba_id)
                    totals["found"] += 1
                else:
                    totals["not_found"] += 1

            merge_field_updates(path, {fba_id: sheet[fba_id] for fba_id in touched_ids}, ["amazon_shipment_status"])
    finally:
        try:
            context.close()
            playwright.stop()
        except Exception:
            pass

    return totals


def format_populate_shipment_status_summary(result: dict) -> str:
    lines = [
        "=" * 60,
        "SHIPMENT STATUS POPULATION SUMMARY",
        "=" * 60,
        f"Backfilled as Delivered (no live check): {result['backfilled_delivered']}",
        f"Checked live (pending shipments):        {result['checked']}",
        f"  -> found:                              {result['found']}",
        f"  -> not found:                          {result['not_found']}",
        f"Skipped (no region config/login failed):  {result.get('skipped', 0)}",
        "=" * 60,
    ]
    return "\n".join(lines)
