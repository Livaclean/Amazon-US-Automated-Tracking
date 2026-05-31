# verify_tracking.py
import re
import logging
from dataclasses import dataclass, field

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
                lines.append("\n  Re-uploaded successfully:")
                for item in r.re_uploaded:
                    lines.append(f"    {item['fba_id']}  — {item['filled']} tracking ID(s) filled")

            if r.still_incomplete:
                lines.append("\n  Still incomplete after re-upload:")
                for item in r.still_incomplete:
                    lines.append(
                        f"    {item['fba_id']}  — {item['filled']} of {item['total']} slots filled "
                        f"(fewer tracking IDs than fields)"
                    )

            if r.missing_in_sheet:
                lines.append("\n  Tracking missing in sheet (in sheet but no usable tracking ID):")
                for item in r.missing_in_sheet:
                    lines.append(f"    {item['fba_id']}  — {item['reason']}")

            if r.not_in_sheet:
                lines.append("\n  Not in sheet (FBA ID not found in sheet at all):")
                for fba_id in r.not_in_sheet:
                    lines.append(f"    {fba_id}")

        lines.append("")

    lines.append(SEP)
    return "\n".join(lines)
