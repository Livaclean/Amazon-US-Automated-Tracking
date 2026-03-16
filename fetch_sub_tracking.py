# fetch_sub_tracking.py
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# UPS: 1Z + 16 alphanumeric chars
UPS_PATTERN = re.compile(r"\b(1Z[0-9A-Z]{16})\b", re.IGNORECASE)
# FedEx: 12-22 digits
FEDEX_PATTERN = re.compile(r"\b(\d{22}|\d{20}|\d{15}|\d{12})\b")

UPS_TRACK_URL = "https://www.ups.com/track?tracknum={tracking}&loc=en_US"
FEDEX_TRACK_URL = "https://www.fedex.com/wtrk/track/?tracknumbers={tracking}"

UPS_SELECTORS = [
    "[data-testid*='package-tracking-number']",
    "[data-testid*='trackingNumber']",
    ".pkg-info__tracking-number",
    ".package-tracking-number",
    "[class*='trackingNum']",
]

FEDEX_SELECTORS = [
    "[data-testid*='tracking-number']",
    ".tracking-number-value",
    "[class*='trackingNum']",
    ".shipment-piece-tracking",
    "[aria-label*='tracking number']",
]


def normalize_carrier(carrier_name) -> str:
    """Returns 'ups', 'fedex', or 'unknown'."""
    if not carrier_name:
        return "unknown"
    name = str(carrier_name).lower().strip()
    if "ups" in name or "united parcel" in name:
        return "ups"
    if "fedex" in name or "federal express" in name:
        return "fedex"
    return "unknown"


def extract_ups_tracking_from_text(text: str, exclude: str = None) -> list:
    """Extracts all 1Z-format UPS tracking numbers from text."""
    if not text:
        return []
    matches = UPS_PATTERN.findall(text.upper())
    result = []
    for m in matches:
        if exclude and m.upper() == str(exclude).upper():
            continue
        result.append(m.upper())
    return result


def extract_fedex_tracking_from_text(text: str, exclude: str = None) -> list:
    """Extracts all 12-22 digit FedEx tracking numbers from text."""
    if not text:
        return []
    result = []
    for m in FEDEX_PATTERN.findall(text):
        if exclude and m == str(exclude):
            continue
        result.append(m)
    return result


def deduplicate_tracking_numbers(numbers: list) -> list:
    """Removes duplicates while preserving order."""
    if not numbers:
        return []
    seen = set()
    result = []
    for n in numbers:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _try_selectors(page, selectors: list) -> list:
    """Tries each CSS selector, returns text content of all matching elements."""
    for selector in selectors:
        try:
            elements = page.query_selector_all(selector)
            texts = []
            for el in elements:
                content = el.text_content().strip()
                if content:
                    texts.append(content)
            if texts:
                logger.debug(f"Selector '{selector}' matched {len(texts)} elements")
                return texts
        except Exception:
            continue
    return []


def _handle_captcha(page) -> None:
    """Pauses if a CAPTCHA is detected and waits for user to solve it."""
    try:
        has_captcha = (
            "captcha" in page.url.lower()
            or bool(page.query_selector("iframe[title*='challenge']"))
        )
    except Exception:
        has_captcha = False
    if has_captcha:
        logger.warning("CAPTCHA detected — please solve it manually in the browser")
        print("\n  ACTION REQUIRED: Solve the CAPTCHA in the browser window, then press Enter.")
        input()
        page.wait_for_load_state("load", timeout=20000)


# Named constant for log truncation
_LOG_PAGE_TEXT_LIMIT = 20000  # cap to avoid huge debug files


def _fetch_sub_tracking(
    page,
    url: str,
    selectors: list,
    extractor,
    main_tracking: str,
    log_prefix: str,
    load_wait_ms: int,
    logs_folder: str = None,
) -> list:
    """Shared scraper logic for UPS and FedEx tracking pages."""
    logger.info(f"  Loading: {url}")
    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("load", timeout=30000)
        # Wait for SPA content to render (carrier sites load data asynchronously)
        page.wait_for_timeout(load_wait_ms)
        # If page still shows "Loading", wait up to 10 more seconds for content
        try:
            page.wait_for_function(
                "!document.body?.innerText?.includes('\\nLoading\\n')",
                timeout=10000,
            )
        except Exception:
            pass  # Proceed with whatever content is available
    except Exception as e:
        logger.error(f"  Failed to load page: {e}")
        return []

    _handle_captcha(page)

    # Stage 1: CSS selectors
    texts = _try_selectors(page, selectors)
    if texts:
        numbers = []
        for t in texts:
            numbers.extend(extractor(t, exclude=main_tracking))
        if numbers:
            logger.info(f"  Found {len(numbers)} sub-IDs via selectors")
            return deduplicate_tracking_numbers(numbers)

    # Stage 2: Regex fallback on full page text
    try:
        page_text = page.inner_text("body")
        numbers = deduplicate_tracking_numbers(extractor(page_text, exclude=main_tracking))
        logger.info(f"  Found {len(numbers)} sub-IDs via regex fallback")
        if logs_folder:
            Path(logs_folder).joinpath(f"{log_prefix}_{main_tracking}.txt").write_text(
                page_text[:_LOG_PAGE_TEXT_LIMIT], encoding="utf-8"
            )
        return numbers
    except Exception as e:
        logger.error(f"  Page text extraction failed: {e}")
        return []


def _click_ups_next_page(page) -> bool:
    """
    Tries to click the 'Next' pagination button in the UPS
    'Other Packages in this Shipment' section.
    Returns True if a next-page button was found and clicked, False otherwise.
    """
    next_candidates = [
        "button:has-text('Next')",
        "a:has-text('Next')",
        "[aria-label*='Next' i]",
        "button[aria-label*='next' i]",
        "li.next > a",
        ".pagination-next",
        "button:has-text('>')",
    ]
    for selector in next_candidates:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible() and el.is_enabled():
                el.click()
                page.wait_for_timeout(2000)
                logger.debug(f"  Clicked next-page button: {selector}")
                return True
        except Exception:
            pass
    return False


def fetch_ups_sub_tracking(page, main_tracking: str, logs_folder: str = None) -> list:
    """
    Opens UPS tracking page, clicks 'Other Packages in this Shipment',
    and extracts sub-package tracking IDs from that section across all pages.
    Falls back to full page regex if the section is not found.
    """
    url = UPS_TRACK_URL.format(tracking=main_tracking)
    logger.info(f"  Loading: {url}")

    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("load", timeout=30000)
        page.wait_for_timeout(4000)
    except Exception as e:
        logger.error(f"  Failed to load page: {e}")
        return []

    _handle_captcha(page)

    # Click "Other Packages in this Shipment" to expand the list
    section_found = False
    try:
        el = page.get_by_text("Other Packages in this Shipment", exact=False).first
        if el and el.is_visible():
            el.click()
            logger.debug("  Clicked 'Other Packages in this Shipment'")
            page.wait_for_timeout(2000)
            section_found = True
    except Exception:
        pass

    if not section_found:
        logger.debug("  'Other Packages in this Shipment' section not found on page")

    # Extract from the section across all pages
    try:
        all_numbers = []
        marker = "Other Packages in this Shipment"

        for page_num in range(1, 20):  # up to 20 pages
            page_text = page.inner_text("body")
            idx = page_text.find(marker)
            if idx >= 0:
                section = page_text[idx:idx + 5000]
                numbers = extract_ups_tracking_from_text(section, exclude=main_tracking)
                new = [n for n in numbers if n not in all_numbers]
                if new:
                    all_numbers.extend(new)
                    logger.info(f"  Page {page_num}: +{len(new)} sub-IDs (total {len(all_numbers)})")
                else:
                    logger.debug(f"  Page {page_num}: no new IDs found")

                # Try to go to next page
                if not _click_ups_next_page(page):
                    logger.debug(f"  No next-page button found — done at page {page_num}")
                    break
            else:
                # Section not visible on this page — stop
                break

        if all_numbers:
            result = deduplicate_tracking_numbers(all_numbers)
            logger.info(f"  Total {len(result)} unique sub-IDs across all pages")
            return result

        logger.debug("  No IDs from section — falling back to full page regex")

        # Fallback: full page regex
        page_text = page.inner_text("body")
        if logs_folder:
            Path(logs_folder).joinpath(f"ups_page_{main_tracking}.txt").write_text(
                page_text[:_LOG_PAGE_TEXT_LIMIT], encoding="utf-8"
            )
        numbers = deduplicate_tracking_numbers(
            extract_ups_tracking_from_text(page_text, exclude=main_tracking)
        )
        logger.info(f"  Found {len(numbers)} sub-IDs via regex fallback")
        return numbers
    except Exception as e:
        logger.error(f"  Page text extraction failed: {e}")
        return []


FEDEX_LOGIN_URL = "https://www.fedex.com/en-us/home.html"
FEDEX_LOGIN_SELECTORS = [
    "input[id*='userId']",
    "input[name*='userId']",
    "input[type='email']",
    "#login-btn",
    "[data-testid*='sign-in']",
]


def _is_fedex_login_page(page) -> bool:
    """Returns True if the current page is a FedEx login page."""
    try:
        url = page.url.lower()
        if "/signin" in url or "/login" in url or "sso.fedex.com" in url:
            return True
        for s in FEDEX_LOGIN_SELECTORS:
            try:
                if page.query_selector(s):
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _is_fedex_logged_in(page) -> bool:
    """Returns True if the FedEx page shows a logged-in state (checks visible text only)."""
    try:
        # Use inner_text (visible text only) to avoid false positives from hidden HTML
        visible = page.inner_text("body").lower()
        # Logged-in state shows sign out / log out in the visible navigation
        if "sign out" in visible or "log out" in visible:
            return True
        # Not logged in if it shows sign-up/login CTA prominently in visible text
        if "sign up or log in" in visible or "sign in" in visible:
            return False
    except Exception:
        pass
    return False


def check_fedex_login(page) -> None:
    """
    Checks FedEx login status but does NOT block — trkqual URLs work without login
    for shipments in 'Label created' state, so we just log and proceed.
    """
    try:
        page.goto(FEDEX_LOGIN_URL, timeout=20000)
        page.wait_for_load_state("load", timeout=15000)
        page.wait_for_timeout(2000)
    except Exception as e:
        logger.debug(f"check_fedex_login: navigation error: {e}")

    if _is_fedex_logged_in(page):
        logger.info("FedEx: already logged in")
    else:
        logger.info("FedEx: not logged in — proceeding anyway (trkqual URLs work without login)")


def fetch_fedex_sub_tracking(page, main_tracking: str, logs_folder: str = None) -> list:
    """
    Opens FedEx tracking page using trkqual URL (requires FedEx login).
    Navigates via FedEx home first to establish proper session/cookies,
    then loads the tracking page with trkqual to avoid bot detection.
    """
    # Navigate via FedEx home first to warm up session cookies
    try:
        page.goto("https://www.fedex.com/en-us/home.html", timeout=20000)
        page.wait_for_load_state("load", timeout=15000)
        page.wait_for_timeout(2000)
    except Exception:
        pass

    # Build trkqual URL — try FDEG (Ground) first, then FDXE (Express)
    trkqual_candidates = [
        f"12030~{main_tracking}~FDEG",
        f"12029~{main_tracking}~FDEG",
        f"10800~{main_tracking}~FDXE",
        f"10800~{main_tracking}~FXSP",
    ]

    logger.info(f"  Trying {len(trkqual_candidates)} trkqual candidates for {main_tracking}")

    section_markers = [
        "Piece Shipment",
        "Shipment pieces",
        "Package pieces",
        "All pieces",
        "Other packages",
        "Pieces in this shipment",
        "packages in this shipment",
    ]

    for trkqual in trkqual_candidates:
        candidate_url = f"https://www.fedex.com/fedextrack/?trknbr={main_tracking}&trkqual={trkqual}"
        logger.info(f"  Loading: {candidate_url}")
        try:
            page.goto(candidate_url, timeout=30000)
            page.wait_for_load_state("load", timeout=30000)
            page.wait_for_timeout(6000)
        except Exception as e:
            logger.debug(f"  trkqual {trkqual} load error: {e}")
            continue

        # Hard failures — skip to next candidate
        try:
            body_text = page.inner_text("body")
        except Exception:
            continue
        if "system-error" in page.url or "can't find" in body_text.lower():
            logger.debug(f"  trkqual {trkqual} — error page, trying next")
            continue

        # Soft signal: "Shipment is N of M pieces" on main page = correct trkqual
        has_piece_indicator = "of " in body_text.lower() and "piece" in body_text.lower()
        logger.info(f"  trkqual {trkqual} loaded — piece indicator: {has_piece_indicator}")

        _handle_captcha(page)

        # Click "View more details" to reach the piece table
        try:
            el = page.get_by_text("View more details", exact=False).first
            if el and el.is_visible():
                el.click()
                logger.debug("  Clicked 'View more details'")
                page.wait_for_load_state("load", timeout=15000)
                page.wait_for_timeout(10000)
        except Exception:
            pass

        # Try expanding "Show all pieces" type buttons
        for btn_text in ["Show all", "View all", "See all", "pieces", "Packages"]:
            try:
                el = page.get_by_text(btn_text, exact=False).first
                if el and el.is_visible():
                    el.click()
                    logger.debug(f"  Clicked '{btn_text}'")
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                pass

        try:
            page_text = page.inner_text("body")
        except Exception:
            continue

        # Search section markers for sub-IDs
        for marker in section_markers:
            idx = page_text.lower().find(marker.lower())
            if idx >= 0:
                section = page_text[idx:idx + 3000]
                numbers = deduplicate_tracking_numbers(
                    extract_fedex_tracking_from_text(section, exclude=main_tracking)
                )
                logger.info(f"  Found {len(numbers)} sub-IDs from '{marker}' section")
                if numbers:
                    return numbers
                logger.debug(f"  Section '{marker}' found but no IDs — trying next trkqual")
                break

        # Regex fallback on full page
        numbers = deduplicate_tracking_numbers(
            extract_fedex_tracking_from_text(page_text, exclude=main_tracking)
        )
        logger.info(f"  Found {len(numbers)} sub-IDs via regex (trkqual {trkqual})")
        if numbers:
            if logs_folder:
                Path(logs_folder).joinpath(f"fedex_page_{main_tracking}.txt").write_text(
                    page_text[:_LOG_PAGE_TEXT_LIMIT], encoding="utf-8"
                )
            return numbers

        # 0 found with this trkqual — try the next one
        logger.debug(f"  trkqual {trkqual} gave 0 sub-IDs — trying next candidate")

    # All candidates exhausted with 0 results
    logger.warning(f"  All trkqual candidates tried — 0 sub-IDs found for {main_tracking}")
    try:
        page_text = page.inner_text("body")
    except Exception:
        return []
    if logs_folder:
        Path(logs_folder).joinpath(f"fedex_page_{main_tracking}.txt").write_text(
            page_text[:_LOG_PAGE_TEXT_LIMIT], encoding="utf-8"
        )
    numbers = deduplicate_tracking_numbers(
        extract_fedex_tracking_from_text(page_text, exclude=main_tracking)
    )
    logger.info(f"  Found {len(numbers)} sub-IDs via regex fallback")
    return numbers


def fetch_sub_tracking_ids(page, main_tracking: str, carrier: str,
                            logs_folder: str = None) -> list:
    """Dispatches to UPS or FedEx scraper based on carrier string."""
    normalized = normalize_carrier(carrier)
    if normalized == "ups":
        return fetch_ups_sub_tracking(page, main_tracking, logs_folder)
    elif normalized == "fedex":
        return fetch_fedex_sub_tracking(page, main_tracking, logs_folder)
    else:
        logger.warning(f"  Unknown carrier '{carrier}' for tracking {main_tracking} — skipping")
        return []


def get_all_sub_tracking(page, tracking_entries: list, logs_folder: str = None) -> list:
    """
    For a list of {"tracking": "...", "carrier": "..."} dicts,
    fetches and returns a flat deduplicated list of all sub-tracking IDs.
    """
    if not tracking_entries:
        return []
    all_ids = []
    for entry in tracking_entries:
        tracking = entry.get("tracking", "")
        carrier = entry.get("carrier", "")
        if not tracking:
            continue
        logger.info(f"  Fetching sub-tracking for {tracking} ({carrier})")
        sub_ids = fetch_sub_tracking_ids(page, tracking, carrier, logs_folder)
        if not sub_ids:
            logger.warning(f"  No sub-IDs found for {tracking} — check carrier website manually")
        all_ids.extend(sub_ids)
    return deduplicate_tracking_numbers(all_ids)
