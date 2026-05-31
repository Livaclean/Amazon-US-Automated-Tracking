# verify_tracking.py
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

FBA_ID_RE = re.compile(r'FBA[A-Z0-9]{6,}')

QUEUE_SELECTORS = {
    "status_filter_button": [
        "button:has-text('Status')",
        "[data-testid*='status-filter']",
        "[aria-label*='Status' i]",
        "label:has-text('Status')",
    ],
    "ready_to_ship_option": [
        "li:has-text('Ready to ship')",
        "option:has-text('Ready to ship')",
        "[data-value='READY_TO_SHIP']",
        "label:has-text('Ready to ship')",
        "span:has-text('Ready to ship')",
        "div:has-text('Ready to ship')",
    ],
    "apply_button": [
        "button:has-text('Apply')",
        "[data-testid*='apply']",
        "button:has-text('Filter')",
        "input[value='Apply']",
    ],
    "missing_tracking_badge": [
        "[data-testid*='missing-tracking']",
        "span:has-text('Missing tracking number')",
        "span:has-text('Missing Tracking ID')",
        "span:has-text('Missing tracking ID')",
        "[class*='missing-tracking']",
        "div:has-text('Missing Tracking')",
    ],
    "next_page_button": [
        "button[aria-label='Next page']",
        "button[aria-label='Next']",
        "button:has-text('Next')",
        "[data-testid*='pagination-next']",
        "a:has-text('Next')",
        "li.next a",
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


def discover_queue_page(page, amazon_url: str, logs_folder: str) -> None:
    """
    Dumps all interactive elements from the shipping queue page.
    Run with --discover-queue on first use to find real Amazon selectors,
    then update QUEUE_SELECTORS if needed.
    """
    queue_url = f"{amazon_url}/gp/ssof/shipping-queue.html#fbashipment"
    logger.info(f"Discovery: navigating to {queue_url}")
    try:
        page.goto(queue_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        logger.warning(f"discover_queue_page: navigation failed: {e}")
        return
    page.wait_for_timeout(3000)

    folder = Path(logs_folder) / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        page.screenshot(path=str(folder / f"queue_discovery_{ts}.png"))
    except Exception:
        pass

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
