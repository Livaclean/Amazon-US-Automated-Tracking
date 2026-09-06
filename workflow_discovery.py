# workflow_discovery.py
"""
Discovers each shipment's Send to Amazon workflow ID by visiting its shipment
page and following the "Send to Amazon (view)" link. A single workflow can
cover several sibling shipments (split across destinations) -- when it does,
all of them are recorded with the same workflow ID in one pass, so we never
have to open the workflow page again for the others.
"""
import logging
import re

logger = logging.getLogger(__name__)

_WORKFLOW_ID_PATTERN = re.compile(r"[?&]wf=([^&]+)")

# "FBA19K4G0K6R - IMO1" -> ("FBA19K4G0K6R", "IMO1"); also matches AWD's
# "STAR-RJSSXHFN6ZS5X - MCC1" -- the split point is the spaced " - " before
# the FC code, not the hyphen inside "STAR-...", which has no surrounding spaces.
_SIBLING_LINK_PATTERN = re.compile(r"^(FBA\S+|STAR-\S+)\s+-\s+(\S+)$")


def _extract_workflow_id_from_url(url: str) -> str:
    """Extracts the workflow ID from a Send to Amazon URL's ?wf= query param. None if absent."""
    match = _WORKFLOW_ID_PATTERN.search(url)
    return match.group(1) if match else None


def _extract_sibling_fba_ids(link_texts: list) -> list:
    """
    Parses a workflow page's "Track shipment" link texts (e.g. "FBA19K4G0K6R -
    IMO1") into bare FBA IDs, stripping the FC-code suffix. Non-matching text
    (site-wide nav links, etc.) is ignored. Order preserved, deduplicated.
    """
    seen = set()
    result = []
    for text in link_texts:
        match = _SIBLING_LINK_PATTERN.match(text.strip())
        if match:
            fba_id = match.group(1)
            if fba_id not in seen:
                seen.add(fba_id)
                result.append(fba_id)
    return result


def _extract_workflow_from_page(page) -> dict:
    """
    Assumes `page` is already on a Send to Amazon workflow page. Extracts the
    workflow ID from the URL and the sibling FBA IDs from the page's links.
    Always returns {"workflow_id": str | None, "fba_ids": [str, ...]} -- never
    None -- so every caller has exactly one not-found convention (workflow_id
    is None) to check, whether it calls this directly or goes through
    discover_workflow_for_shipment.
    """
    workflow_id = _extract_workflow_id_from_url(page.url)
    if not workflow_id:
        return {"workflow_id": None, "fba_ids": []}
    link_texts = page.locator("a").all_inner_texts()
    fba_ids = _extract_sibling_fba_ids(link_texts)
    return {"workflow_id": workflow_id, "fba_ids": fba_ids}


def discover_workflow_for_shipment(page, fba_id: str, base_url: str) -> dict:
    """
    Navigates to fba_id's shipment page, reads its Amazon shipment-status
    badge, then clicks "Send to Amazon (view)" and extracts the workflow ID
    and all sibling FBA IDs listed on that workflow's page.

    Always returns {"workflow_id": str | None, "fba_ids": [str, ...],
    "amazon_shipment_status": str | None} -- workflow_id is None if the
    shipment page didn't load, had no "Send to Amazon (view)" link (not part
    of a Send to Amazon workflow), or no workflow ID was found in the URL.
    amazon_shipment_status is only None when the page itself never loaded --
    it's still reported even when there's no workflow to discover, since it's
    read from the shipment page directly, before the workflow link is checked.
    """
    from upload_tracking import navigate_to_shipment, fetch_shipment_status

    if not navigate_to_shipment(page, fba_id, base_url):
        return {"workflow_id": None, "fba_ids": [], "amazon_shipment_status": None}

    amazon_shipment_status = fetch_shipment_status(page)

    link = page.get_by_text("Send to Amazon (view)", exact=True)
    if link.count() == 0:
        logger.info(f"  {fba_id}: no 'Send to Amazon (view)' link -- not part of a tracked workflow")
        return {"workflow_id": None, "fba_ids": [], "amazon_shipment_status": amazon_shipment_status}

    link.first.click()
    try:
        # The "Workflow ID:" summary renders quickly, but the sibling "Track
        # shipment" links load a few seconds later via a separate fetch --
        # wait for them specifically rather than guessing at a fixed delay.
        page.wait_for_selector("text=Track shipment", timeout=15000)
    except Exception:
        logger.debug(f"  {fba_id}: 'Track shipment' links never appeared (single-shipment workflow, or slow page)")
    page.wait_for_timeout(500)

    workflow = _extract_workflow_from_page(page)
    if workflow["workflow_id"] is None:
        logger.warning(f"  {fba_id}: landed on 'Send to Amazon (view)' but found no workflow ID in the URL")
    return {**workflow, "amazon_shipment_status": amazon_shipment_status}


def _process_region_discoveries(page, base_url: str, fba_ids: list, sheet: dict, touched: set = None) -> dict:
    """
    Discovers workflow IDs for `fba_ids` (all in the same region, `page` already
    logged in to `base_url`), writing results directly into `sheet` (mutated in
    place). Skips any FBA ID that already has a workflow_id -- including ones
    resolved via an earlier sibling discovery within this same call. Returns
    {"discovered": int, "resolved_via_sibling": int, "unresolved": int}.

    If `touched` (a set) is given, every FBA ID whose sheet row is actually
    written to is added to it -- lets the caller save only the rows this pass
    changed instead of the whole in-memory snapshot, narrowing (not
    eliminating) the chance of clobbering a concurrent writer's unrelated
    updates to other rows.
    """
    discovered = 0
    resolved_via_sibling = 0
    unresolved = 0

    for fba_id in fba_ids:
        if sheet.get(fba_id, {}).get("workflow_id"):
            continue  # resolved via an earlier sibling in this loop

        result = discover_workflow_for_shipment(page, fba_id, base_url)

        # Read from fba_id's own page visit regardless of whether it turned
        # out to have a workflow -- siblings resolved below never got their
        # own page visit, so their status isn't touched here. A truthy check
        # (not just "is not None") matters here: Amazon's status badge can
        # expose an empty label attribute before it's finished hydrating
        # client-side, and an empty string is never a real status worth
        # overwriting a previously-good value with.
        if result["amazon_shipment_status"] and fba_id in sheet:
            sheet[fba_id]["amazon_shipment_status"] = result["amazon_shipment_status"]
            if touched is not None:
                touched.add(fba_id)

        workflow_id = result["workflow_id"]
        if not workflow_id:
            unresolved += 1
            continue

        sibling_ids = result["fba_ids"] or [fba_id]
        for sib in sibling_ids:
            if sib not in sheet or sheet[sib].get("workflow_id"):
                continue
            sheet[sib]["workflow_id"] = workflow_id
            if touched is not None:
                touched.add(sib)
            if sib == fba_id:
                discovered += 1
            else:
                resolved_via_sibling += 1

    return {"discovered": discovered, "resolved_via_sibling": resolved_via_sibling, "unresolved": unresolved}


def _pending_fba_ids_by_region(sheet: dict) -> dict:
    """
    Groups FBA IDs needing workflow-ID discovery by region: those missing a
    workflow_id and not already marked Delivered in either status column. A
    workflow_id only exists to support delivery-window sync, which skips
    Delivered shipments entirely -- discovering one for them is wasted work.
    """
    from master_sheet import is_carrier_delivered

    pending_by_region = {}
    for fba_id, entry in sheet.items():
        if entry.get("workflow_id"):
            continue
        if is_carrier_delivered(entry):
            continue
        pending_by_region.setdefault(entry.get("region"), []).append(fba_id)
    return pending_by_region


def run_workflow_discovery(config: dict) -> dict:
    """
    Discovers workflow IDs for every shipment in the master sheet that doesn't
    have one yet and isn't already Delivered, one region at a time (its own
    browser login). Saves the master sheet after each region so progress
    survives a mid-run crash.
    Returns {"discovered", "resolved_via_sibling", "unresolved"}.
    """
    from master_sheet import load_master_sheet, merge_field_updates, MASTER_SHEET_PATH_DEFAULT
    from upload_tracking import create_browser_context
    from run import wait_for_login

    path = config.get("master_sheet_path", MASTER_SHEET_PATH_DEFAULT)
    sheet = load_master_sheet(path)
    region_by_name = {r["name"]: r for r in config.get("regions", [])}

    pending_by_region = _pending_fba_ids_by_region(sheet)

    totals = {"discovered": 0, "resolved_via_sibling": 0, "unresolved": 0}
    if not pending_by_region:
        return totals

    playwright, context = create_browser_context(config)
    try:
        page = context.new_page()
        for region_name, fba_ids in pending_by_region.items():
            region = region_by_name.get(region_name)
            if not region:
                logger.warning(f"No config entry for region {region_name!r} -- skipping {len(fba_ids)} shipment(s)")
                totals["unresolved"] += len(fba_ids)
                continue

            base_url = region["amazon_url"]
            if not wait_for_login(page, region_name, base_url):
                logger.warning(f"Could not log in to {region_name} -- skipping {len(fba_ids)} shipment(s)")
                totals["unresolved"] += len(fba_ids)
                continue

            touched = set()
            region_totals = _process_region_discoveries(page, base_url, fba_ids, sheet, touched=touched)
            for key in totals:
                totals[key] += region_totals[key]
            merge_field_updates(path, {fba_id: sheet[fba_id] for fba_id in touched}, ["workflow_id", "amazon_shipment_status"])
    finally:
        try:
            context.close()
            playwright.stop()
        except Exception:
            pass

    return totals


def format_workflow_discovery_summary(result: dict) -> str:
    lines = [
        "=" * 60,
        "WORKFLOW ID DISCOVERY SUMMARY",
        "=" * 60,
        f"Discovered directly:      {result['discovered']}",
        f"Resolved via sibling:     {result['resolved_via_sibling']}",
        f"Unresolved:               {result['unresolved']}",
        "=" * 60,
    ]
    return "\n".join(lines)
