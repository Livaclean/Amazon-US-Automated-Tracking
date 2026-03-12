# upload_tracking.py
import logging
import time
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Layered fallback selectors — try in order.
# Run `python run.py --discover` on first use to see real Amazon selectors,
# then update these lists if needed.
SELECTORS = {
    "add_tracking_button": [
        "button:has-text('Add tracking')",
        "button:has-text('Add Tracking')",
        "button:has-text('Enter tracking')",
        "button:has-text('Enter Tracking')",
        "[data-testid*='add-tracking']",
        "[data-testid*='addTracking']",
        "button[aria-label*='tracking' i]",
        "a:has-text('Add tracking')",
        "a:has-text('Enter tracking')",
    ],
    "tracking_input": [
        "input[placeholder*='tracking' i]",
        "input[name*='tracking' i]",
        "input[data-testid*='tracking']",
        "input[aria-label*='tracking' i]",
    ],
    "save_button": [
        "button:has-text('Save')",
        "button:has-text('Confirm')",
        "button:has-text('Submit')",
        "[data-testid*='save']",
        "[data-testid*='confirm']",
        "button[type='submit']",
    ],
}

LOGIN_SELECTORS = ["input#ap_email", "input[name='email']", "form[name='signIn']"]
ALREADY_EXISTS_TEXTS = ["already exists", "already been added", "duplicate"]
NOT_FOUND_TEXTS = ["not found", "does not exist", "invalid shipment"]


def _screenshot(page, step_name: str, logs_folder: str) -> None:
    """Saves a screenshot to logs/screenshots/ on error."""
    try:
        folder = Path(logs_folder) / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Sanitize step_name: replace characters illegal in Windows filenames.
        safe_step = "".join(c if c.isalnum() or c in "-_." else "_" for c in step_name)
        page.screenshot(path=str(folder / f"{ts}_{safe_step}.png"))
    except Exception as e:
        logger.debug(f"Screenshot failed ({step_name}): {e}")


def _try_click(page, selectors: list, timeout: int = 5000) -> Optional[str]:
    """Clicks first matching visible selector. Returns matched selector or None."""
    for s in selectors:
        try:
            el = page.wait_for_selector(s, timeout=timeout, state="visible")
            el.click()
            return s
        except Exception as e:
            logger.debug(f"Selector '{s}' did not match: {e}")
            continue
    return None


def _try_fill(page, selectors: list, value: str, timeout: int = 5000) -> Optional[str]:
    """Fills first matching visible input. Returns matched selector or None."""
    for s in selectors:
        try:
            el = page.wait_for_selector(s, timeout=timeout, state="visible")
            el.click()
            el.fill(value)
            return s
        except Exception as e:
            logger.debug(f"Selector '{s}' did not match: {e}")
            continue
    return None


def _page_contains(page, texts: list) -> Optional[str]:
    """Returns first text found in page content (case-insensitive), or None."""
    try:
        content = page.content().lower()
        for t in texts:
            if t.lower() in content:
                return t
    except Exception as e:
        logger.debug(f"_page_contains: page.content() failed: {e}")
    return None


def create_browser_context(config: dict):
    """
    Launches Chrome using a dedicated automation profile (non-default path).
    This avoids Chrome's remote-debugging restriction on the default user data dir.
    On first run the profile directory is created fresh — the user will need to
    log in to Amazon Seller Central manually (the session is then saved for future runs).
    Chrome must be fully closed before calling this.
    Returns (playwright_instance, context).
    Raises RuntimeError with a user-friendly message if Chrome profile is locked.
    """
    from playwright.sync_api import sync_playwright
    # Create the profile directory if it doesn't exist yet
    Path(config["chrome_profile_path"]).mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=config["chrome_profile_path"],
            channel="chrome",
            headless=config.get("headless", False),
            args=[
                f"--profile-directory={config.get('chrome_profile_name', 'Default')}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
            ],
            viewport={"width": 1280, "height": 900},
            slow_mo=500,
        )
        # Remove webdriver flag to avoid bot detection on carrier sites
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return playwright, context
    except Exception as e:
        playwright.stop()
        msg = str(e).lower()
        if "already in use" in msg or "user data directory" in msg or "lock" in msg:
            raise RuntimeError(
                "Chrome profile is already in use.\n"
                "Please close Google Chrome completely and try again."
            ) from e
        raise


def _is_login_page(page) -> bool:
    """Returns True if the current page is an Amazon login/sign-in page."""
    try:
        url = page.url.lower()
        if "/ap/signin" in url or "ap_signin" in url or "/signin" in url:
            return True
        # Also check DOM selectors as fallback
        for s in LOGIN_SELECTORS:
            try:
                if page.query_selector(s):
                    return True
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"_is_login_page check failed: {e}")
    return False


def _wait_for_login(page) -> None:
    """
    Blocks until the current page is no longer an Amazon login/auth page (up to 5 min).
    Handles multi-step auth (password → OTP/2FA) by waiting for a settled authenticated URL.
    """
    logger.warning("Amazon login required — waiting for manual login in browser window")
    print("\n" + "=" * 60)
    print("ACTION REQUIRED: Log in to Amazon Seller Central in the")
    print("browser window. Complete ALL login steps (password + OTP if prompted).")
    print("Script will continue automatically once you are logged in")
    print("(waiting up to 5 minutes).")
    print("=" * 60)
    # Wait for URL to settle on a non-auth page for 3 consecutive seconds
    settled = 0
    for _ in range(300):
        page.wait_for_timeout(1000)
        url = page.url.lower()
        is_auth = (
            "/ap/signin" in url or "ap_signin" in url or "/signin" in url
            or "/ap/mfa" in url or "/ap/otp" in url or "/ap/cvf" in url
            or "/ap/challenge" in url or "/ap/verify" in url
        )
        if not is_auth:
            settled += 1
            if settled >= 3:
                logger.info("Amazon login detected (stable) — continuing")
                break
        else:
            settled = 0
    # Small extra wait for session cookies to be fully written
    page.wait_for_timeout(1500)


def check_login_status(page, base_url: str) -> None:
    """
    Navigates to an authenticated Amazon Seller Central page to detect login state.
    If login page detected, waits up to 5 minutes for the user to log in manually.
    """
    # /inventory always requires authentication (unlike /fba/inbound-shipments list)
    auth_check_url = f"{base_url}/inventory"
    try:
        page.goto(auth_check_url, timeout=20000)
        page.wait_for_load_state("load", timeout=15000)
    except Exception as e:
        logger.debug(f"check_login_status: navigation exception: {e}")

    logger.debug(f"check_login_status: current URL = {page.url}")
    if _is_login_page(page):
        _wait_for_login(page)
    else:
        logger.info("Amazon Seller Central: already logged in")


def navigate_to_shipment(page, fba_id: str, base_url: str) -> bool:
    """
    Navigates to the tracking page for a shipment.
    AWD shipments (STAR- prefix) use /awd/inbound-shipment/{id}/tracking_spd.
    FBA shipments use /fba/inbound-shipment/summary/{id}/tracking.
    Handles login redirects by retrying up to 3 times.
    Returns True on success, False if shipment not found or nav failed.
    """
    if fba_id.startswith("STAR-"):
        url = f"{base_url}/awd/inbound-shipment/{fba_id}/tracking_spd"
    else:
        url = f"{base_url}/fba/inbound-shipment/summary/{fba_id}/tracking"
    logger.info(f"  -> {url}")

    for attempt in range(3):
        try:
            page.goto(url, timeout=20000)
            page.wait_for_load_state("load", timeout=15000)
        except Exception as e:
            logger.error(f"  Navigation failed (attempt {attempt + 1}): {e}")
            return False

        logger.debug(f"  URL after navigation: {page.url}")

        if not _is_login_page(page):
            break  # Successfully loaded the shipment page

        logger.warning(f"  Redirected to login (attempt {attempt + 1})")
        _wait_for_login(page)
        # Loop will re-navigate to the FBA URL on next iteration
    else:
        logger.error(f"  Could not navigate past login after 3 attempts for {fba_id}")
        return False

    if _page_contains(page, NOT_FOUND_TEXTS):
        logger.warning(f"  Shipment {fba_id} not found on Amazon")
        return False
    return True


def _get_tracking_frame(page):
    """
    Returns the iframe frame object for the tracking input section.
    FBA: iframe URL contains /fba/inbound/summary/tracking
    Returns None if not found (AWD pages have no iframe — use main page directly).
    """
    for frame in page.frames:
        if "/fba/inbound/summary/tracking" in frame.url:
            return frame
    return None


def _get_tracking_context(page, fba_id: str):
    """
    Returns the frame or main page to use for querying tracking inputs.
    FBA: uses the tracking iframe.
    AWD (STAR- prefix): no iframe — inputs are in the main page.
    Returns None if no suitable context found.
    """
    frame = _get_tracking_frame(page)
    if frame:
        return frame
    if fba_id.startswith("STAR-"):
        return page
    return None


def upload_tracking_to_shipment(page, sub_ids: list, fba_id: str, config: dict, force: bool = False) -> dict:
    """
    Fills tracking numbers into the per-box input fields in the tracking iframe,
    then clicks 'Update all' to save.
    sub_ids: list of tracking numbers to fill (one per box row, in order).
    force: if True, overwrite inputs that already have a value instead of skipping them.
    Returns result dict with counts.
    """
    logs_folder = config.get("logs_folder", "logs")
    delay = config.get("delay_between_shipments_seconds", 2)

    result = {
        "fba_id": fba_id,
        "status": "success",
        "total": len(sub_ids),
        "succeeded": 0,
        "already_existed": 0,
        "failed": 0,
        "tracking_results": [],
    }

    if not sub_ids:
        result["status"] = "skipped"
        return result

    # Safety: skip any tracking IDs containing "/" (invalid/placeholder values)
    filtered_ids = [t for t in sub_ids if "/" not in str(t)]
    if len(filtered_ids) < len(sub_ids):
        skipped = [t for t in sub_ids if "/" in str(t)]
        logger.warning(f"  Skipping {len(skipped)} tracking ID(s) containing '/': {skipped}")
    sub_ids = filtered_ids
    if not sub_ids:
        logger.warning(f"  All tracking IDs for {fba_id} contained '/' — skipping upload")
        result["status"] = "skipped"
        return result

    # Wait for the tracking iframe (FBA) or main page (AWD) to be ready
    tracking_frame = None
    for _ in range(20):  # up to 10 seconds
        tracking_frame = _get_tracking_context(page, fba_id)
        if tracking_frame:
            break
        page.wait_for_timeout(500)

    if not tracking_frame:
        logger.error(f"  Could not find tracking context for {fba_id}")
        _screenshot(page, f"no_iframe_{fba_id}", logs_folder)
        result["status"] = "failed"
        result["failed"] = len(sub_ids)
        return result

    # Wait for the DOM to fully render its inputs
    try:
        tracking_frame.wait_for_selector("input[placeholder*='auto fill'], input[placeholder*='Enter tracking']", timeout=10000)
    except Exception:
        logger.warning(f"  Timed out waiting for tracking inputs in iframe for {fba_id}")

    # Find all empty tracking input fields in the iframe
    try:
        inputs = tracking_frame.query_selector_all("input[placeholder*='auto fill'], input[placeholder*='Enter tracking']")
    except Exception as e:
        logger.error(f"  Could not query tracking inputs in iframe: {e}")
        result["status"] = "failed"
        result["failed"] = len(sub_ids)
        return result

    if not inputs:
        logger.warning(f"  No empty tracking inputs found in iframe for {fba_id} — may already be filled")
        result["status"] = "skipped"
        return result

    logger.info(f"  Found {len(inputs)} empty tracking input(s), have {len(sub_ids)} sub-IDs to fill")

    # Fill inputs: one tracking ID per box row (as many as we have inputs or IDs)
    fill_count = min(len(inputs), len(sub_ids))
    filled = 0
    for i in range(fill_count):
        tid = sub_ids[i]
        inp = inputs[i]
        try:
            existing = (inp.get_attribute("value") or inp.evaluate("e => e.value") or "").strip()
            if existing and not force:
                logger.debug(f"  Box {i+1}: already has value '{existing}' — skipping")
                result["already_existed"] += 1
                result["tracking_results"].append({
                    "tracking_number": tid, "status": "already_exists",
                    "message": f"Box {i+1} already had value"
                })
                continue
            inp.click()
            if existing and force:
                inp.evaluate("e => { e.select(); }")
            inp.fill(tid)
            filled += 1
            logger.debug(f"  Box {i+1}: filled {tid}")
            result["tracking_results"].append({
                "tracking_number": tid, "status": "success", "message": f"Filled in box {i+1}"
            })
        except Exception as e:
            logger.warning(f"  Box {i+1}: failed to fill {tid}: {e}")
            result["tracking_results"].append({
                "tracking_number": tid, "status": "error", "message": str(e)
            })
            result["failed"] += 1

    if filled == 0 and result["already_existed"] == 0:
        logger.error(f"  Could not fill any tracking inputs for {fba_id}")
        _screenshot(page, f"fill_failed_{fba_id}", logs_folder)
        result["status"] = "failed"
        return result

    if filled == 0:
        # Nothing new was filled (all already existed) — button will be disabled, skip click
        logger.info(f"  All inputs already filled for {fba_id} — skipping Update all")
        result["status"] = "skipped"
        result["succeeded"] = 0
        return result

    # Click "Update all" button in the iframe to save
    try:
        update_btn = tracking_frame.query_selector("button:has-text('Update all')")
        if not update_btn:
            # Fallback: any submit button
            update_btn = tracking_frame.query_selector("button[type='submit'], button.button")
        if update_btn:
            update_btn.click()
            logger.info(f"  Clicked 'Update all' — saving {filled} tracking number(s)")
            page.wait_for_timeout(2000)
        else:
            logger.warning(f"  Could not find 'Update all' button for {fba_id}")
            _screenshot(page, f"no_save_btn_{fba_id}", logs_folder)
            result["status"] = "partial"
    except Exception as e:
        logger.error(f"  Error clicking Update all: {e}")
        result["status"] = "partial"

    result["succeeded"] = filled
    if result["failed"] > 0 and filled == 0:
        result["status"] = "failed"
    elif result["failed"] > 0:
        result["status"] = "partial"

    time.sleep(delay)
    return result


def discover_page_elements(page, fba_id: str, base_url: str, logs_folder: str) -> None:
    """
    Dumps all buttons/inputs/links from the shipment page to a text file.
    Use on first run with --discover flag to identify real Amazon selectors.
    """
    navigate_to_shipment(page, fba_id, base_url)
    # Wait for any async JavaScript to render the tracking UI
    page.wait_for_timeout(4000)
    # Save a screenshot so we can see the visual state
    _screenshot(page, f"discovery_{fba_id}", logs_folder)
    output = [f"URL: {page.url}\nTitle: {page.title()}\n\n"]

    output.append("=== BUTTONS ===\n")
    for el in page.query_selector_all("button"):
        try:
            output.append(
                f"  text='{el.text_content().strip()}' | "
                f"class='{el.get_attribute('class')}' | "
                f"data-testid='{el.get_attribute('data-testid')}'\n"
            )
        except Exception as e:
            logger.debug(f"Element attribute read failed: {e}")

    output.append("\n=== INPUTS ===\n")
    for el in page.query_selector_all("input"):
        try:
            output.append(
                f"  type='{el.get_attribute('type')}' | "
                f"name='{el.get_attribute('name')}' | "
                f"placeholder='{el.get_attribute('placeholder')}' | "
                f"aria-label='{el.get_attribute('aria-label')}' | "
                f"class='{el.get_attribute('class')}' | "
                f"value='{el.get_attribute('value')}'\n"
            )
        except Exception as e:
            logger.debug(f"Element attribute read failed: {e}")

    output.append("\n=== CONTENTEDITABLE / TEXTAREAS ===\n")
    for el in page.query_selector_all("[contenteditable], textarea"):
        try:
            output.append(
                f"  tag='{el.evaluate('el => el.tagName')}' | "
                f"text='{el.text_content().strip()[:80]}' | "
                f"class='{el.get_attribute('class')}'\n"
            )
        except Exception as e:
            logger.debug(f"Element attribute read failed: {e}")

    output.append("\n=== TABLE ROWS (first 5, tracking-related) ===\n")
    try:
        rows = page.query_selector_all("table tr, [class*='row'], [class*='Row']")
        count = 0
        for row in rows:
            text = row.text_content().strip()
            if text and ("tracking" in text.lower() or "FBA" in text or count < 3):
                inputs_in_row = row.query_selector_all("input")
                row_inputs = [
                    f"input[type={i.get_attribute('type')} placeholder={i.get_attribute('placeholder')} value={i.get_attribute('value')}]"
                    for i in inputs_in_row
                ]
                output.append(f"  [{count}] text='{text[:100]}' | inputs={row_inputs}\n")
                count += 1
                if count >= 10:
                    break
    except Exception as e:
        logger.debug(f"Table row scan failed: {e}")

    output.append("\n=== ALL TEXT INPUTS (including inside table cells) ===\n")
    try:
        js_inputs = page.evaluate("""() => {
            const inputs = document.querySelectorAll('input[type="text"], input:not([type])');
            return Array.from(inputs).map(i => ({
                placeholder: i.placeholder,
                value: i.value,
                class: i.className,
                name: i.name,
                disabled: i.disabled,
                readOnly: i.readOnly,
                id: i.id
            }));
        }""")
        for inp in js_inputs:
            output.append(f"  {inp}\n")
    except Exception as e:
        logger.debug(f"JS input scan failed: {e}")

    output.append("\n=== IFRAMES + FRAME CONTENTS ===\n")
    try:
        iframes = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('iframe')).map(f => ({
                src: f.src, id: f.id, class: f.className, name: f.name
            }));
        }""")
        for f in iframes:
            output.append(f"  {f}\n")
        # Also try to scan inside frames
        for frame in page.frames:
            if frame.url != page.url and frame.url:
                output.append(f"  [FRAME] url={frame.url}\n")
                try:
                    frame_inputs = frame.query_selector_all("input:not([type='hidden'])")
                    for fi in frame_inputs:
                        output.append(
                            f"    frame-input: placeholder='{fi.get_attribute('placeholder')}' "
                            f"class='{fi.get_attribute('class')}' value='{fi.get_attribute('value')}' "
                            f"type='{fi.get_attribute('type')}'\n"
                        )
                    frame_buttons = frame.query_selector_all("button, [type='submit'], input[type='submit']")
                    for fb in frame_buttons:
                        output.append(
                            f"    frame-button: text='{fb.text_content().strip()[:60]}' "
                            f"class='{fb.get_attribute('class')}' type='{fb.get_attribute('type')}'\n"
                        )
                    frame_links = frame.query_selector_all("a")
                    for fl in frame_links:
                        text = fl.text_content().strip()
                        if text:
                            output.append(f"    frame-link: text='{text[:60]}'\n")
                except Exception as ex:
                    output.append(f"    (scan error: {ex})\n")
    except Exception as e:
        logger.debug(f"Iframe scan failed: {e}")

    output.append("\n=== ELEMENTS WITH 'auto fill' OR 'tracking' PLACEHOLDER ===\n")
    try:
        found = page.evaluate("""() => {
            const all = document.querySelectorAll('*');
            const result = [];
            for (const el of all) {
                const ph = el.getAttribute('placeholder') || '';
                if (ph.toLowerCase().includes('auto fill') || ph.toLowerCase().includes('tracking') || ph.toLowerCase().includes('type here')) {
                    result.push({tag: el.tagName, class: el.className, placeholder: ph, id: el.id});
                }
            }
            return result;
        }""")
        for el in found:
            output.append(f"  {el}\n")
        if not found:
            output.append("  (none found — inputs may be in shadow DOM or iframe)\n")
            # Try Playwright's get_by_placeholder
            try:
                els = page.get_by_placeholder("Type here if not auto fill").all()
                output.append(f"  Playwright get_by_placeholder found: {len(els)} elements\n")
                for i, el in enumerate(els[:5]):
                    output.append(f"  [{i}] tag={el.evaluate('e=>e.tagName')} class={el.get_attribute('class')}\n")
            except Exception as e2:
                output.append(f"  Playwright get_by_placeholder error: {e2}\n")
    except Exception as e:
        logger.debug(f"Placeholder search failed: {e}")

    output.append("\n=== LINKS (anchors) ===\n")
    for el in page.query_selector_all("a"):
        try:
            text = el.text_content().strip()
            if text:
                output.append(
                    f"  text='{text}' | "
                    f"href='{el.get_attribute('href')}' | "
                    f"data-testid='{el.get_attribute('data-testid')}'\n"
                )
        except Exception as e:
            logger.debug(f"Element attribute read failed: {e}")

    dump = Path(logs_folder)
    dump.mkdir(parents=True, exist_ok=True)
    dump = dump / f"page_discovery_{fba_id}.txt"
    dump.write_text("".join(output), encoding="utf-8")
    print(f"\nDiscovery saved to: {dump}")
    print("Review the discovery file and screenshot in logs/ to understand the page structure.")


def check_amazon_tracking_status(page, fba_id: str, config: dict) -> str:
    """
    Checks whether tracking is already uploaded on Amazon for a shipment.
    Returns: "complete", "partial", "empty", or "not_found".
    Does NOT modify anything on Amazon.
    """
    base_url = config.get("amazon_base_url", "https://sellercentral.amazon.com")

    if not navigate_to_shipment(page, fba_id, base_url):
        return "not_found"

    tracking_frame = None
    for _ in range(20):
        tracking_frame = _get_tracking_context(page, fba_id)
        if tracking_frame:
            break
        page.wait_for_timeout(500)

    if not tracking_frame:
        logger.warning(f"  [check] No tracking context found for {fba_id}")
        return "not_found"

    try:
        tracking_frame.wait_for_selector("input[placeholder*='auto fill'], input[placeholder*='Enter tracking']", timeout=10000)
    except Exception:
        logger.warning(f"  [check] Timed out waiting for inputs for {fba_id}")

    try:
        inputs = tracking_frame.query_selector_all("input[placeholder*='auto fill'], input[placeholder*='Enter tracking']")
    except Exception as e:
        logger.warning(f"  [check] Could not query inputs for {fba_id}: {e}")
        return "not_found"

    if not inputs:
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


def get_slot_count(page, fba_id: str, base_url: str) -> int:
    """
    Returns the total number of tracking input slots for a FBA shipment on Amazon.
    Used to distribute shared-tracking pools across multiple FBAs.
    Returns 0 if the shipment page or iframe cannot be found.
    """
    if not navigate_to_shipment(page, fba_id, base_url):
        return 0
    tracking_frame = None
    for _ in range(20):
        tracking_frame = _get_tracking_frame(page)
        if tracking_frame:
            break
        page.wait_for_timeout(500)
    if not tracking_frame:
        return 0
    try:
        tracking_frame.wait_for_selector("input[placeholder*='auto fill'], input[placeholder*='Enter tracking']", timeout=10000)
        inputs = tracking_frame.query_selector_all("input[placeholder*='auto fill'], input[placeholder*='Enter tracking']")
        return len(inputs)
    except Exception:
        return 0


def check_all_shipments_on_amazon(shipments_raw: dict, config: dict, page) -> tuple:
    """
    For each FBA in shipments_raw, checks Amazon to see if tracking is already uploaded.
    Returns: (needs_upload dict, already_complete list)
    Does NOT modify anything on Amazon.
    """
    needs_upload = {}
    already_complete = []

    total = len(shipments_raw)
    for i, (fba_id, entries) in enumerate(shipments_raw.items(), 1):
        logger.info(f"  [{i}/{total}] Checking Amazon: {fba_id}")
        status = check_amazon_tracking_status(page, fba_id, config)
        logger.info(f"  -> Amazon status: {status}")

        if status in ("complete", "not_found"):
            already_complete.append(fba_id)
            label = "complete" if status == "complete" else "not found (delivered/closed)"
            print(f"  [DONE]    {fba_id} — {label}")
        else:
            needs_upload[fba_id] = entries
            label = "partial" if status == "partial" else "pending (empty)"
            print(f"  [PENDING] {fba_id} — {label}")

    return needs_upload, already_complete


def upload_all_shipments(shipments: dict, config: dict, page, force: bool = False) -> list:
    """
    Uploads sub-tracking IDs to Amazon for each FBA shipment.
    shipments: {"FBA123": ["sub_id1", "sub_id2"], ...}
    force: if True, overwrite inputs that already have values.
    Returns list of per-shipment result dicts.
    """
    base_url = config.get("amazon_base_url", "https://sellercentral.amazon.com")
    delay = config.get("delay_between_shipments_seconds", 2)
    results = []

    for fba_id, sub_ids in shipments.items():
        logger.info(f"\nFBA {fba_id}: {len(sub_ids)} sub-tracking IDs")
        r = {
            "fba_id": fba_id,
            "status": "success",
            "total": len(sub_ids),
            "succeeded": 0,
            "already_existed": 0,
            "failed": 0,
            "tracking_results": [],
        }

        if not sub_ids:
            r["status"] = "skipped"
            results.append(r)
            continue

        if not navigate_to_shipment(page, fba_id, base_url):
            r["status"] = "not_found"
            results.append(r)
            continue

        shipment_result = upload_tracking_to_shipment(page, sub_ids, fba_id, config, force=force)
        r["status"] = shipment_result["status"]
        r["succeeded"] = shipment_result["succeeded"]
        r["already_existed"] = shipment_result["already_existed"]
        r["failed"] = shipment_result["failed"]
        r["tracking_results"] = shipment_result.get("tracking_results", [])

        results.append(r)
        time.sleep(delay)

    return results
