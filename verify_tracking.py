# verify_tracking.py
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

FBA_ID_RE = re.compile(r'FBA[A-Z0-9]{6,}')

QUEUE_SELECTORS = {
    # Apply button — class='button', text='Apply' (confirmed from discovery)
    "apply_button": [
        "button.button:has-text('Apply')",
        "button:has-text('Apply')",
    ],
    # FBA links in the queue table use href pattern /fba/inbound-shipment/summary/FBAxxxxx/
    "fba_id_link": [
        "a[href*='/fba/inbound-shipment/summary/FBA']",
    ],
    # Pagination: div.content.pagination-container contains << First < Previous Page N Next >
    "next_page_button": [
        ".pagination-container a:has-text('Next')",
        "div.content.pagination-container a:has-text('Next')",
        ".pagination-container a:has-text('Next >')",
        "[aria-label='Next page']",
        "button:has-text('Next')",
    ],
}


@dataclass
class VerifyResult:
    region: str
    total_checked: int = 0
    total_ok: int = 0
    re_uploaded: list = field(default_factory=list)      # [{"fba_id": str, "filled": int, "total": int}]
    still_incomplete: list = field(default_factory=list) # [{"fba_id": str, "filled": int, "total": int}]
    missing_in_sheet: list = field(default_factory=list) # [{"fba_id": str, "reason": str}]
    not_in_sheet: list = field(default_factory=list)     # [str]


def _is_usable_tracking(value) -> bool:
    """Returns True if value is a non-empty, non-slash tracking string."""
    if value is None:
        return False
    s = str(value).strip()
    return bool(s) and s != "/"


def _cross_reference(fba_ids: list, shipments_all: dict) -> dict:
    """
    Buckets FBA IDs from Amazon's missing-tracking list into 3 groups.
    Returns {"reupload": [...], "missing_in_sheet": [...], "not_in_sheet": [...]}.
    """
    reupload = []
    missing_in_sheet = []
    not_in_sheet = []

    for fba_id in fba_ids:
        if fba_id not in shipments_all:
            not_in_sheet.append(fba_id)
            continue
        entries = shipments_all[fba_id]
        if any(_is_usable_tracking(e.get("tracking")) for e in entries):
            reupload.append(fba_id)
        else:
            sample = str(entries[0].get("tracking", "")).strip() if entries else ""
            reason = 'tracking column is "/"' if sample == "/" else "tracking column blank"
            missing_in_sheet.append({"fba_id": fba_id, "reason": reason})

    return {"reupload": reupload, "missing_in_sheet": missing_in_sheet, "not_in_sheet": not_in_sheet}


def format_verify_summary(results: list) -> str:
    SEP = "=" * 60
    lines = [SEP, "VERIFICATION — Missing Tracking ID Check", SEP]

    for r in results:
        missing_count = (
            len(r.re_uploaded) + len(r.still_incomplete)
            + len(r.missing_in_sheet) + len(r.not_in_sheet)
        )
        lines += [
            f"Region: {r.region}",
            f"  Checked : {r.total_checked} \"Ready to ship\" shipments",
            f"  OK       : {r.total_ok} (tracking complete)",
            f"  Missing  : {missing_count}",
        ]

        if not missing_count:
            lines.append("  All tracking complete.")
        else:
            if r.re_uploaded:
                lines.append("")
                lines.append("  Re-uploaded successfully:")
                for item in r.re_uploaded:
                    lines.append(f"    {item['fba_id']}  — {item['filled']} tracking ID(s) filled")

            if r.still_incomplete:
                lines.append("")
                lines.append("  Still incomplete after re-upload:")
                for item in r.still_incomplete:
                    lines.append(
                        f"    {item['fba_id']}  — {item['filled']} of {item['total']} slots filled "
                        f"(fewer tracking IDs than fields)"
                    )

            if r.missing_in_sheet:
                lines.append("")
                lines.append("  Tracking missing in sheet (in sheet but no usable tracking ID):")
                for item in r.missing_in_sheet:
                    lines.append(f"    {item['fba_id']}  — {item['reason']}")

            if r.not_in_sheet:
                lines.append("")
                lines.append("  Not in sheet (FBA ID not found in sheet at all):")
                for fba_id in r.not_in_sheet:
                    lines.append(f"    {fba_id}")

        lines.append("")

    lines.append(SEP)
    return "\n".join(lines)


def _take_screenshot(page, logs_folder: str, name: str) -> None:
    """Saves a diagnostic screenshot to logs/screenshots/."""
    try:
        folder = Path(logs_folder) / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        page.screenshot(path=str(folder / f"{name}_{ts}.png"))
        logger.info(f"  Screenshot: {name}_{ts}.png")
    except Exception as e:
        logger.debug(f"Screenshot failed ({name}): {e}")


def discover_queue_page(page, amazon_url: str, logs_folder: str) -> None:
    """
    Dumps all interactive elements from the shipping queue page.
    Run with --discover-queue on first use to find real Amazon selectors,
    then update QUEUE_SELECTORS if needed.
    """
    from upload_tracking import _is_login_page, _wait_for_login
    queue_url = f"{amazon_url}/gp/ssof/shipping-queue.html#fbashipment"
    logger.info(f"Discovery: navigating to {queue_url}")
    try:
        page.goto(queue_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        logger.warning(f"discover_queue_page: navigation failed: {e}")
        return
    # Handle login redirect — queue page may require separate auth
    if _is_login_page(page):
        logger.info("discover_queue_page: redirected to login — waiting for manual login...")
        _wait_for_login(page)
        try:
            page.goto(queue_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"discover_queue_page: re-navigation after login failed: {e}")
            return
    page.wait_for_timeout(3000)

    Path(logs_folder).mkdir(parents=True, exist_ok=True)
    folder = Path(logs_folder) / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        page.screenshot(path=str(folder / f"queue_discovery_{ts}.png"))
    except Exception as e:
        logger.debug(f"discover_queue_page: screenshot failed: {e}")

    output = [f"URL: {page.url}\nTitle: {page.title()}\n\n"]

    output.append("=== BUTTONS ===\n")
    for el in page.query_selector_all("button"):
        try:
            output.append(
                f"  text='{el.text_content().strip()}' | "
                f"class='{el.get_attribute('class')}' | "
                f"data-testid='{el.get_attribute('data-testid')}' | "
                f"aria-label='{el.get_attribute('aria-label')}'\n"
            )
        except Exception:
            pass

    output.append("\n=== BADGES / STATUS SPANS ===\n")
    for el in page.query_selector_all("span, [class*='badge'], [class*='status'], [class*='missing']"):
        try:
            text = el.text_content().strip()
            if text and len(text) < 120:
                output.append(
                    f"  text='{text}' | "
                    f"class='{el.get_attribute('class')}' | "
                    f"data-testid='{el.get_attribute('data-testid')}'\n"
                )
        except Exception:
            pass

    output.append("\n=== LINKS (FBA-related) ===\n")
    for el in page.query_selector_all("a"):
        try:
            text = el.text_content().strip()
            href = el.get_attribute("href") or ""
            if "FBA" in text or "fba" in href.lower() or "shipment" in href.lower():
                output.append(f"  text='{text}' | href='{href}'\n")
        except Exception:
            pass

    output.append("\n=== PAGINATION ===\n")
    for el in page.query_selector_all("[class*='pagination'], [class*='pager'], [aria-label*='page' i]"):
        try:
            output.append(
                f"  tag='{el.evaluate('e => e.tagName')}' | "
                f"text='{el.text_content().strip()[:80]}' | "
                f"class='{el.get_attribute('class')}' | "
                f"aria-label='{el.get_attribute('aria-label')}'\n"
            )
        except Exception:
            pass

    dump_path = Path(logs_folder) / "queue_discovery.txt"
    dump_path.write_text("".join(output), encoding="utf-8")
    print(f"\nQueue discovery saved to: {dump_path}")
    print("Review the file and screenshot in logs/ to confirm selectors before running --verify.")


def _navigate_to_queue_page(page, amazon_url: str) -> bool:
    """Navigates to the FBA shipping queue page, handling login redirects. Returns True on success."""
    from upload_tracking import _is_login_page, _wait_for_login
    queue_url = f"{amazon_url}/gp/ssof/shipping-queue.html#fbashipment"
    for attempt in range(3):
        try:
            page.goto(queue_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"_navigate_to_queue_page failed: {e}")
            return False
        if _is_login_page(page):
            logger.warning(f"_navigate_to_queue_page: redirected to login (attempt {attempt + 1})")
            _wait_for_login(page)
            continue
        # Wait for the SPA to render — look for the toggle or any shipment link
        try:
            page.wait_for_selector(
                ".missing-tracking-info-toggle-group, a[href*='/fba/inbound-shipment/summary/FBA']",
                timeout=15000,
                state="visible",
            )
        except Exception:
            # Page loaded but expected elements not yet visible — give it more time
            page.wait_for_timeout(3000)
        return True
    logger.error("_navigate_to_queue_page: could not navigate past login after 3 attempts")
    return False


def _apply_ready_to_ship_filter(page, logs_folder: str = "logs") -> bool:
    """
    Clicks the 'Only show shipments with missing tracking information' toggle.
    Discovery found this is simpler and more reliable than the Status → Ready to ship flow.
    Returns True if the toggle was clicked, False if not found.
    NOTE: Run --discover-queue if this fails, to re-check selectors.
    """
    logger.info(f"_apply_ready_to_ship_filter: current URL = {page.url}")

    # Step 1: Click the Status filter trigger
    # From discovery: the trigger is a span/button with text 'Status' in the filter bar
    logger.info("  Step 1: opening Status filter dropdown...")
    status_clicked = False
    try:
        status_clicked = page.evaluate("""() => {
            // Find the deepest element whose exact text is 'Status' — that's the trigger
            const all = [...document.querySelectorAll('*')];
            for (const el of all) {
                if (el.children.length === 0 && el.textContent.trim() === 'Status') {
                    el.scrollIntoView();
                    el.click();
                    return true;
                }
            }
            return false;
        }""")
    except Exception as e:
        logger.debug(f"JS Status click failed: {e}")

    if not status_clicked:
        logger.warning("  Status filter trigger not found")
        _take_screenshot(page, logs_folder, "verify_status_not_found")
        return False

    page.wait_for_timeout(1500)
    logger.info("  Status dropdown opened — taking screenshot...")
    _take_screenshot(page, logs_folder, "verify_after_status_click")

    # Step 2: Select "Ready to ship" option
    logger.info("  Step 2: selecting 'Ready to ship'...")
    rts_clicked = False
    try:
        rts_clicked = page.evaluate("""() => {
            const all = [...document.querySelectorAll('*')];
            for (const el of all) {
                if (el.children.length === 0 && el.textContent.trim() === 'Ready to ship') {
                    el.scrollIntoView();
                    el.click();
                    return true;
                }
            }
            return false;
        }""")
    except Exception as e:
        logger.debug(f"JS Ready to ship click failed: {e}")

    if not rts_clicked:
        logger.warning("  'Ready to ship' option not found")
        _take_screenshot(page, logs_folder, "verify_rts_not_found")
        return False

    page.wait_for_timeout(800)
    logger.info("  'Ready to ship' selected — taking screenshot...")
    _take_screenshot(page, logs_folder, "verify_after_rts_select")

    # Step 3: Click Apply
    logger.info("  Step 3: clicking Apply...")
    apply_clicked = False
    for selector in QUEUE_SELECTORS["apply_button"]:
        try:
            el = page.wait_for_selector(selector, timeout=5000, state="visible")
            el.click()
            apply_clicked = True
            logger.debug(f"  Apply clicked via: {selector}")
            break
        except Exception:
            continue

    if not apply_clicked:
        # JS fallback for Apply
        try:
            apply_clicked = page.evaluate("""() => {
                const btns = [...document.querySelectorAll('button.button, button')];
                const btn = btns.find(b => b.textContent.trim() === 'Apply');
                if (btn) { btn.click(); return true; }
                return false;
            }""")
        except Exception as e:
            logger.debug(f"JS Apply click failed: {e}")

    if not apply_clicked:
        logger.warning("  Apply button not found")
        _take_screenshot(page, logs_folder, "verify_apply_not_found")
        return False

    page.wait_for_timeout(3000)
    logger.info("  Filter applied — taking screenshot to confirm...")
    _take_screenshot(page, logs_folder, "verify_filter_applied")

    # Verify: count FBA links after filter — should be fewer than unfiltered
    try:
        count = len(page.query_selector_all("a[href*='/fba/inbound-shipment/summary/FBA']"))
        logger.info(f"  FBA links visible after filter: {count}")
    except Exception:
        pass

    return True
    # Screenshot to diagnose what the page looks like when toggle is not found
    try:
        folder = Path(logs_folder) / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        page.screenshot(path=str(folder / f"verify_toggle_not_found_{ts}.png"))
        logger.info(f"Screenshot saved: verify_toggle_not_found_{ts}.png")
    except Exception as e:
        logger.debug(f"Screenshot failed: {e}")
    logger.warning("_apply_ready_to_ship_filter: missing tracking toggle not found")
    return False


def _collect_missing_fba_ids_on_page(page) -> list:
    """
    Extracts FBA IDs from shipment links on the current (filtered) page.
    After the missing-tracking toggle is active, every link on the page points
    to a shipment with missing tracking — FBA IDs come directly from link hrefs.
    """
    fba_ids = []
    seen: set = set()
    try:
        links = page.query_selector_all("a[href*='/fba/inbound-shipment/summary/FBA']")
        for link in links:
            try:
                href = link.get_attribute("href") or ""
                match = FBA_ID_RE.search(href)
                if match:
                    fba_id = match.group()
                    if fba_id not in seen:
                        seen.add(fba_id)
                        fba_ids.append(fba_id)
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"_collect_missing_fba_ids_on_page: link query failed: {e}")
    return fba_ids


def _collect_all_missing_fba_ids(page) -> list:
    """
    Paginates through ALL pages of the filtered queue and collects every
    FBA ID that has a 'Missing Tracking ID' badge. Hard requirement: no page is skipped.
    """
    all_fba_ids = []
    page_num = 1
    max_pages = 50  # safety ceiling — queue will never realistically exceed this

    while page_num <= max_pages:
        logger.info(f"  Queue page {page_num}: collecting missing-tracking FBA IDs...")
        ids_on_page = _collect_missing_fba_ids_on_page(page)
        logger.info(f"  Page {page_num}: found {len(ids_on_page)} FBA(s) with missing tracking")
        all_fba_ids.extend(ids_on_page)

        next_clicked = False
        for selector in QUEUE_SELECTORS["next_page_button"]:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible() and btn.is_enabled():
                    btn.click()
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    page.wait_for_timeout(1000)
                    page_num += 1
                    next_clicked = True
                    logger.debug(f"  Navigated to page {page_num} via: {selector}")
                    break
            except Exception:
                continue

        if not next_clicked:
            logger.info(f"  No more pages after page {page_num}.")
            break

    if page_num > max_pages:
        logger.warning(f"  Reached max_pages limit ({max_pages}) — stopping pagination")

    return list(dict.fromkeys(all_fba_ids))


def _reupload_fba(page, fba_id: str, entries: list, config: dict) -> dict:
    """
    Runs full carrier scrape then uploads tracking for one FBA with missing tracking.
    Returns {"fba_id": str, "status": str, "filled": int, "total": int}.
    """
    from fetch_sub_tracking import get_all_sub_tracking
    from upload_tracking import navigate_to_shipment, upload_tracking_to_shipment

    base_url = config.get("amazon_base_url", "https://sellercentral.amazon.com")
    logs_folder = config.get("logs_folder", "logs")

    logger.info(f"  [verify] Re-uploading {fba_id}: running carrier scrape...")
    try:
        sub_ids = get_all_sub_tracking(page, entries, logs_folder)
    except Exception as e:
        logger.warning(f"  [verify] Carrier scrape failed for {fba_id}: {e}")
        sub_ids = []

    main_ids = [e["tracking"] for e in entries if _is_usable_tracking(e.get("tracking"))]
    all_ids = list(dict.fromkeys(main_ids + sub_ids))

    if not navigate_to_shipment(page, fba_id, base_url):
        logger.warning(f"  [verify] Shipment {fba_id} not found on Amazon")
        return {"fba_id": fba_id, "status": "not_found", "filled": 0, "total": 0}

    upload_result = upload_tracking_to_shipment(page, all_ids, fba_id, config)

    filled = upload_result.get("already_existed", 0) + upload_result.get("succeeded", 0)
    total = filled + upload_result.get("empty_slots_remaining", 0)

    return {
        "fba_id": fba_id,
        "status": upload_result.get("status", "failed"),
        "filled": filled,
        "total": total,
    }


def run_verify(page, region: dict, config: dict, shipments_all: dict) -> VerifyResult:
    """
    Main entry point. Checks Amazon's shipping queue for this region,
    cross-references against shipments_all, re-uploads where possible.
    Returns a populated VerifyResult.
    """
    from upload_tracking import _is_login_page, _wait_for_login

    region_name = region["name"]
    amazon_url = region["amazon_url"]
    result = VerifyResult(region=region_name)

    logger.info(f"[{region_name}] Starting verification pass...")

    region_config = dict(config)
    region_config["amazon_base_url"] = amazon_url

    # Navigate to queue
    if not _navigate_to_queue_page(page, amazon_url):
        logger.warning(f"[{region_name}] Could not load shipping queue — skipping verify")
        return result

    # Handle login redirect
    if _is_login_page(page):
        _wait_for_login(page)

    # Apply Ready to ship filter
    if not _apply_ready_to_ship_filter(page, config.get("logs_folder", "logs")):
        logger.warning(f"[{region_name}] Could not apply Ready to ship filter — skipping verify")
        return result

    # Collect all FBA IDs with missing tracking across all pages
    missing_fba_ids = _collect_all_missing_fba_ids(page)
    result.total_checked = len(missing_fba_ids)
    logger.info(f"[{region_name}] Found {len(missing_fba_ids)} FBA(s) with missing tracking")
    print(f"\n[{region_name}] Verify: {len(missing_fba_ids)} FBA(s) with missing tracking badge")

    if not missing_fba_ids:
        result.total_ok = 0
        return result

    # Cross-reference against sheet
    buckets = _cross_reference(missing_fba_ids, shipments_all)

    result.not_in_sheet = buckets["not_in_sheet"]
    result.missing_in_sheet = buckets["missing_in_sheet"]

    # Re-upload FBAs that have usable tracking in the sheet
    for fba_id in buckets["reupload"]:
        entries = shipments_all[fba_id]
        print(f"  [{region_name}] Re-uploading {fba_id}...")
        reup = _reupload_fba(page, fba_id, entries, region_config)

        if reup["status"] in ("success", "partial") and reup["total"] > 0 and reup["filled"] == reup["total"]:
            result.re_uploaded.append(reup)
        elif reup["status"] in ("success", "partial") and reup["total"] > 0 and reup["filled"] < reup["total"]:
            result.still_incomplete.append(reup)
        else:
            result.still_incomplete.append(reup)

        # Update done cache for fully-filled shipments
        if reup["filled"] == reup["total"] and reup["total"] > 0:
            done_cache_file = Path(config["logs_folder"]) / f"completed_fba_{region_name}.txt"
            try:
                existing = set()
                if done_cache_file.exists():
                    existing = {
                        line.strip()
                        for line in done_cache_file.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    }
                existing.add(fba_id)
                done_cache_file.write_text("\n".join(sorted(existing)), encoding="utf-8")
            except Exception as e:
                logger.warning(f"[{region_name}] Could not update done cache for {fba_id}: {e}")

    return result
