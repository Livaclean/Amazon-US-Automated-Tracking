# Verify Missing Tracking (v2.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every upload run, check all "Ready to ship" FBAs across all regions for a "Missing Tracking ID" badge, cross-reference against the sheet, re-upload with full carrier scrape where possible, and report all outcomes in the final summary.

**Architecture:** A new `verify_tracking.py` module owns all queue-page navigation, cross-reference logic, and re-upload orchestration. `run.py` gains a `--verify` flag for standalone use and auto-calls verify after every normal upload run. All existing upload/carrier-scrape functions are reused as-is — no changes to `upload_tracking.py`, `fetch_sub_tracking.py`, or `parse_excel.py`.

**Tech Stack:** Python 3.10+, Playwright (already in use), pytest, dataclasses (stdlib)

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `verify_tracking.py` | `VerifyResult` dataclass, pure cross-reference logic, queue page navigation + pagination, re-upload orchestration, summary formatting, discovery helper |
| Create | `tests/test_verify_tracking.py` | Unit tests for pure logic; integration tests for browser functions |
| Modify | `run.py` | Add `--verify` flag; auto-verify after upload loop; standalone `--verify` mode; print verify summary; add `--discover-queue` flag |

---

## Task 1: VerifyResult dataclass + pure cross-reference logic

**Files:**
- Create: `verify_tracking.py`
- Create: `tests/test_verify_tracking.py`

- [ ] **Step 1: Write failing tests for `_is_usable_tracking`**

Create `tests/test_verify_tracking.py`:

```python
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from verify_tracking import _is_usable_tracking, _cross_reference, VerifyResult


@pytest.mark.unit
def test_is_usable_tracking_none():
    assert _is_usable_tracking(None) is False


@pytest.mark.unit
def test_is_usable_tracking_empty_string():
    assert _is_usable_tracking("") is False


@pytest.mark.unit
def test_is_usable_tracking_whitespace():
    assert _is_usable_tracking("   ") is False


@pytest.mark.unit
def test_is_usable_tracking_slash():
    assert _is_usable_tracking("/") is False


@pytest.mark.unit
def test_is_usable_tracking_valid_ups():
    assert _is_usable_tracking("1Z999AA10123456784") is True


@pytest.mark.unit
def test_is_usable_tracking_valid_fedex():
    assert _is_usable_tracking("123456789012") is True
```

- [ ] **Step 2: Write failing tests for `_cross_reference`**

Append to `tests/test_verify_tracking.py`:

```python
@pytest.mark.unit
def test_cross_reference_not_in_sheet():
    result = _cross_reference(["FBA999"], {})
    assert result["not_in_sheet"] == ["FBA999"]
    assert result["reupload"] == []
    assert result["missing_in_sheet"] == []


@pytest.mark.unit
def test_cross_reference_valid_tracking_goes_to_reupload():
    shipments = {"FBA001": [{"tracking": "1Z999AA10123456784", "carrier": "UPS"}]}
    result = _cross_reference(["FBA001"], shipments)
    assert result["reupload"] == ["FBA001"]
    assert result["missing_in_sheet"] == []
    assert result["not_in_sheet"] == []


@pytest.mark.unit
def test_cross_reference_blank_tracking_goes_to_missing_in_sheet():
    shipments = {"FBA002": [{"tracking": "", "carrier": "UPS"}]}
    result = _cross_reference(["FBA002"], shipments)
    assert len(result["missing_in_sheet"]) == 1
    assert result["missing_in_sheet"][0]["fba_id"] == "FBA002"
    assert "blank" in result["missing_in_sheet"][0]["reason"]


@pytest.mark.unit
def test_cross_reference_slash_tracking_goes_to_missing_in_sheet():
    shipments = {"FBA003": [{"tracking": "/", "carrier": "UPS"}]}
    result = _cross_reference(["FBA003"], shipments)
    assert len(result["missing_in_sheet"]) == 1
    assert result["missing_in_sheet"][0]["fba_id"] == "FBA003"
    assert '/' in result["missing_in_sheet"][0]["reason"]


@pytest.mark.unit
def test_cross_reference_mixed_entries():
    """If any entry has usable tracking, FBA goes to reupload."""
    shipments = {"FBA004": [
        {"tracking": "/", "carrier": "UPS"},
        {"tracking": "1Z999AA10123456784", "carrier": "UPS"},
    ]}
    result = _cross_reference(["FBA004"], shipments)
    assert result["reupload"] == ["FBA004"]


@pytest.mark.unit
def test_verify_result_defaults():
    r = VerifyResult(region="US")
    assert r.total_checked == 0
    assert r.re_uploaded == []
    assert r.still_incomplete == []
    assert r.missing_in_sheet == []
    assert r.not_in_sheet == []
```

- [ ] **Step 3: Run tests to confirm they fail**

```
pytest tests/test_verify_tracking.py -m unit -v
```

Expected: `ModuleNotFoundError: No module named 'verify_tracking'`

- [ ] **Step 4: Create `verify_tracking.py` with `VerifyResult`, `_is_usable_tracking`, `_cross_reference`**

```python
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
```

- [ ] **Step 5: Run tests to confirm they pass**

```
pytest tests/test_verify_tracking.py -m unit -v
```

Expected: all 11 tests PASS

- [ ] **Step 6: Commit**

```
git add verify_tracking.py tests/test_verify_tracking.py
git commit -m "feat: add VerifyResult dataclass and cross-reference pure logic"
```

---

## Task 2: Summary formatting

**Files:**
- Modify: `verify_tracking.py`
- Modify: `tests/test_verify_tracking.py`

- [ ] **Step 1: Write failing tests for `format_verify_summary`**

Append to `tests/test_verify_tracking.py`:

```python
from verify_tracking import format_verify_summary


@pytest.mark.unit
def test_format_verify_summary_all_ok():
    r = VerifyResult(region="US", total_checked=5, total_ok=5)
    out = format_verify_summary([r])
    assert "VERIFICATION" in out
    assert "US" in out
    assert "All tracking complete" in out


@pytest.mark.unit
def test_format_verify_summary_re_uploaded():
    r = VerifyResult(region="US", total_checked=3, total_ok=2)
    r.re_uploaded = [{"fba_id": "FBA001", "filled": 3, "total": 3}]
    out = format_verify_summary([r])
    assert "FBA001" in out
    assert "Re-uploaded successfully" in out
    assert "3" in out


@pytest.mark.unit
def test_format_verify_summary_still_incomplete():
    r = VerifyResult(region="US", total_checked=2, total_ok=1)
    r.still_incomplete = [{"fba_id": "FBA002", "filled": 2, "total": 4}]
    out = format_verify_summary([r])
    assert "FBA002" in out
    assert "Still incomplete" in out
    assert "2 of 4" in out


@pytest.mark.unit
def test_format_verify_summary_missing_in_sheet():
    r = VerifyResult(region="CA", total_checked=2, total_ok=1)
    r.missing_in_sheet = [{"fba_id": "FBA003", "reason": 'tracking column is "/"'}]
    out = format_verify_summary([r])
    assert "FBA003" in out
    assert "Tracking missing in sheet" in out


@pytest.mark.unit
def test_format_verify_summary_not_in_sheet():
    r = VerifyResult(region="US", total_checked=2, total_ok=1)
    r.not_in_sheet = ["FBA004"]
    out = format_verify_summary([r])
    assert "FBA004" in out
    assert "Not in sheet" in out


@pytest.mark.unit
def test_format_verify_summary_multiple_regions():
    r1 = VerifyResult(region="US", total_checked=10, total_ok=10)
    r2 = VerifyResult(region="CA", total_checked=5, total_ok=5)
    out = format_verify_summary([r1, r2])
    assert "US" in out
    assert "CA" in out
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_verify_tracking.py -m unit -k "format" -v
```

Expected: `ImportError: cannot import name 'format_verify_summary'`

- [ ] **Step 3: Implement `format_verify_summary` in `verify_tracking.py`**

Add after `_cross_reference`:

```python
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```
pytest tests/test_verify_tracking.py -m unit -v
```

Expected: all 17 tests PASS

- [ ] **Step 5: Commit**

```
git add verify_tracking.py tests/test_verify_tracking.py
git commit -m "feat: add format_verify_summary"
```

---

## Task 3: Queue discovery helper + `--discover-queue` flag

**Files:**
- Modify: `verify_tracking.py`
- Modify: `run.py`

No unit tests for this task — it is a diagnostic/discovery helper.

- [ ] **Step 1: Add `discover_queue_page` to `verify_tracking.py`**

Add after `format_verify_summary`:

```python
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
```

- [ ] **Step 2: Add `--discover-queue` to `run.py` argparse**

In `run.py`, find the argparse block and add after the `--discover` argument:

```python
parser.add_argument(
    "--discover-queue",
    action="store_true",
    help="Dump Amazon shipping queue page elements to logs/ (run before first --verify to find selectors)",
)
```

- [ ] **Step 3: Handle `--discover-queue` in `run.py` main()**

Add the import at the top of the import block inside `main()` (after `from upload_tracking import ...`):

```python
from verify_tracking import discover_queue_page
```

Add the discovery handler after the existing `if args.discover:` block (around line 544), before the `if args.check_only:` block:

```python
if args.discover_queue:
    first_region = configured_regions[0]
    region_url = first_region["amazon_url"]
    logger.info(f"Queue discovery mode: opening shipping queue for {first_region['name']}")
    print(f"Queue discovery: you may need to log in to {region_url} manually.")
    wait_for_login(page, first_region["name"], region_url)
    discover_queue_page(page, region_url, config["logs_folder"])
    print("\nQueue discovery complete. Review logs/queue_discovery.txt and the screenshot.")
    return
```

- [ ] **Step 4: Run existing unit tests to confirm nothing broke**

```
pytest tests/ -m unit -v
```

Expected: all unit tests PASS

- [ ] **Step 5: Commit**

```
git add verify_tracking.py run.py
git commit -m "feat: add --discover-queue flag and discover_queue_page helper"
```

---

## Task 4: Queue navigation, filter, and paginated badge collection

**Files:**
- Modify: `verify_tracking.py`
- Modify: `tests/test_verify_tracking.py`

- [ ] **Step 1: Write integration tests**

Append to `tests/test_verify_tracking.py`:

```python
from verify_tracking import (
    _navigate_to_queue_page,
    _apply_ready_to_ship_filter,
    _collect_all_missing_fba_ids,
)


@pytest.mark.integration
def test_navigate_to_queue_page(browser_page, test_logger):
    """Should navigate to the shipping queue without errors."""
    result = _navigate_to_queue_page(browser_page, "https://sellercentral.amazon.com")
    test_logger.info(f"Queue page URL: {browser_page.url}, result: {result}")
    assert result is True
    assert "shipping-queue" in browser_page.url or "ssof" in browser_page.url


@pytest.mark.integration
def test_apply_ready_to_ship_filter(browser_page, test_logger):
    """Should apply Status=Ready to ship filter without errors."""
    _navigate_to_queue_page(browser_page, "https://sellercentral.amazon.com")
    result = _apply_ready_to_ship_filter(browser_page)
    test_logger.info(f"Filter applied: {result}, URL: {browser_page.url}")
    assert result is True


@pytest.mark.integration
def test_collect_all_missing_fba_ids_returns_list(browser_page, test_logger):
    """Should return a list (possibly empty) of FBA IDs with missing tracking."""
    _navigate_to_queue_page(browser_page, "https://sellercentral.amazon.com")
    _apply_ready_to_ship_filter(browser_page)
    fba_ids = _collect_all_missing_fba_ids(browser_page)
    test_logger.info(f"Found {len(fba_ids)} FBA IDs with missing tracking: {fba_ids[:5]}")
    assert isinstance(fba_ids, list)
    for fba_id in fba_ids:
        assert fba_id.startswith("FBA"), f"Unexpected ID format: {fba_id}"
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_verify_tracking.py -m integration -v
```

Expected: `ImportError: cannot import name '_navigate_to_queue_page'`

- [ ] **Step 3: Implement `_navigate_to_queue_page`, `_apply_ready_to_ship_filter`, `_collect_all_missing_fba_ids` in `verify_tracking.py`**

Add after `discover_queue_page`:

```python
def _navigate_to_queue_page(page, amazon_url: str) -> bool:
    """Navigates to the FBA shipping queue page. Returns True on success."""
    queue_url = f"{amazon_url}/gp/ssof/shipping-queue.html#fbashipment"
    try:
        page.goto(queue_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        return True
    except Exception as e:
        logger.warning(f"_navigate_to_queue_page failed: {e}")
        return False


def _apply_ready_to_ship_filter(page) -> bool:
    """
    Clicks the Status filter, selects 'Ready to ship', and clicks Apply.
    Returns True if the filter was applied, False if any selector was not found.
    NOTE: Run --discover-queue first if this fails, to identify real Amazon selectors.
    """
    # Step 1: open the Status filter
    for selector in QUEUE_SELECTORS["status_filter_button"]:
        try:
            el = page.wait_for_selector(selector, timeout=5000, state="visible")
            el.click()
            page.wait_for_timeout(800)
            logger.debug(f"Status filter opened via: {selector}")
            break
        except Exception:
            continue
    else:
        logger.warning("_apply_ready_to_ship_filter: Status filter button not found")
        return False

    # Step 2: select 'Ready to ship'
    for selector in QUEUE_SELECTORS["ready_to_ship_option"]:
        try:
            el = page.wait_for_selector(selector, timeout=5000, state="visible")
            el.click()
            page.wait_for_timeout(500)
            logger.debug(f"'Ready to ship' selected via: {selector}")
            break
        except Exception:
            continue
    else:
        logger.warning("_apply_ready_to_ship_filter: 'Ready to ship' option not found")
        return False

    # Step 3: click Apply
    for selector in QUEUE_SELECTORS["apply_button"]:
        try:
            el = page.wait_for_selector(selector, timeout=5000, state="visible")
            el.click()
            page.wait_for_timeout(2000)
            logger.debug(f"Apply clicked via: {selector}")
            return True
        except Exception:
            continue

    logger.warning("_apply_ready_to_ship_filter: Apply button not found")
    return False


def _collect_missing_fba_ids_on_page(page) -> list:
    """
    Extracts FBA IDs from rows with a 'Missing Tracking ID' badge on the current page.
    Uses JavaScript DOM traversal for reliability across Amazon UI variations.
    """
    try:
        fba_ids = page.evaluate("""() => {
            const FBA_RE = /FBA[A-Z0-9]{6,}/;
            const results = [];
            const BADGE_TEXTS = [
                'missing tracking number', 'missing tracking id',
                'missing tracking', 'add tracking',
            ];
            // Find all text nodes containing a badge phrase
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
            const badgeEls = new Set();
            let node;
            while ((node = walker.nextNode())) {
                const text = node.textContent.toLowerCase();
                if (BADGE_TEXTS.some(t => text.includes(t))) {
                    badgeEls.add(node.parentElement);
                }
            }
            // Also find by data-testid and class
            document.querySelectorAll(
                '[data-testid*="missing-tracking"], [class*="missing-tracking"]'
            ).forEach(el => badgeEls.add(el));

            for (const badge of badgeEls) {
                let p = badge;
                for (let i = 0; i < 12; i++) {
                    if (!p || !p.parentElement) break;
                    p = p.parentElement;
                    const tag = p.tagName.toLowerCase();
                    const cls = (p.className || '').toLowerCase();
                    if (tag === 'tr' || tag === 'li' ||
                        cls.includes('row') || cls.includes('shipment') ||
                        cls.includes('item')) {
                        const match = p.textContent.match(/FBA[A-Z0-9]{6,}/);
                        if (match) {
                            results.push(match[0]);
                            break;
                        }
                    }
                }
            }
            return [...new Set(results)];
        }""")
        return fba_ids if isinstance(fba_ids, list) else []
    except Exception as e:
        logger.warning(f"_collect_missing_fba_ids_on_page: JS evaluation failed: {e}")
        return []


def _collect_all_missing_fba_ids(page) -> list:
    """
    Paginates through ALL pages of the filtered queue and collects every
    FBA ID that has a 'Missing Tracking ID' badge. Hard requirement: no page is skipped.
    """
    all_fba_ids = []
    page_num = 1

    while True:
        logger.info(f"  Queue page {page_num}: collecting missing-tracking FBA IDs...")
        ids_on_page = _collect_missing_fba_ids_on_page(page)
        logger.info(f"  Page {page_num}: found {len(ids_on_page)} FBA(s) with missing tracking")
        all_fba_ids.extend(ids_on_page)

        # Check for next page button
        next_clicked = False
        for selector in QUEUE_SELECTORS["next_page_button"]:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible() and btn.is_enabled():
                    btn.click()
                    page.wait_for_timeout(2000)
                    page_num += 1
                    next_clicked = True
                    logger.debug(f"  Navigated to page {page_num} via: {selector}")
                    break
            except Exception:
                continue

        if not next_clicked:
            logger.info(f"  No more pages after page {page_num - 1}.")
            break

    return list(dict.fromkeys(all_fba_ids))  # deduplicate, preserve order
```

- [ ] **Step 4: Run integration tests (requires browser + Amazon login)**

```
pytest tests/test_verify_tracking.py -m integration -v
```

Expected: all 3 integration tests PASS (or SKIP if not logged in)

If tests fail due to selector mismatches, run `python run.py --discover-queue` first, review `logs/queue_discovery.txt`, and update `QUEUE_SELECTORS` in `verify_tracking.py` accordingly.

- [ ] **Step 5: Run unit tests to confirm nothing regressed**

```
pytest tests/test_verify_tracking.py -m unit -v
```

Expected: all 17 unit tests PASS

- [ ] **Step 6: Commit**

```
git add verify_tracking.py tests/test_verify_tracking.py
git commit -m "feat: add queue navigation, filter, and paginated badge collection"
```

---

## Task 5: Re-upload orchestration

**Files:**
- Modify: `verify_tracking.py`
- Modify: `tests/test_verify_tracking.py`

- [ ] **Step 1: Write integration test for `_reupload_fba`**

Append to `tests/test_verify_tracking.py`:

```python
from verify_tracking import _reupload_fba


@pytest.mark.integration
def test_reupload_fba_not_found(browser_page, tmp_config, test_logger):
    """Should return status 'not_found' for a non-existent FBA ID."""
    result = _reupload_fba(
        browser_page,
        fba_id="FBA_NONEXISTENT_TEST_ID",
        entries=[{"tracking": "1Z999AA10123456784", "carrier": "UPS"}],
        config=tmp_config,
    )
    test_logger.info(f"_reupload_fba result: {result}")
    assert result["fba_id"] == "FBA_NONEXISTENT_TEST_ID"
    assert result["status"] == "not_found"
```

- [ ] **Step 2: Run test to confirm it fails**

```
pytest tests/test_verify_tracking.py -m integration -k "reupload" -v
```

Expected: `ImportError: cannot import name '_reupload_fba'`

- [ ] **Step 3: Implement `_reupload_fba` in `verify_tracking.py`**

Add this import at the top of `verify_tracking.py` (with the other stdlib imports):

```python
# verify_tracking.py — add to imports at top of file
```

Add `_reupload_fba` function after `_collect_all_missing_fba_ids`:

```python
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
```

- [ ] **Step 4: Run integration test**

```
pytest tests/test_verify_tracking.py -m integration -k "reupload" -v
```

Expected: PASS (or SKIP if not logged in)

- [ ] **Step 5: Run all unit tests**

```
pytest tests/test_verify_tracking.py -m unit -v
```

Expected: all 17 unit tests PASS

- [ ] **Step 6: Commit**

```
git add verify_tracking.py tests/test_verify_tracking.py
git commit -m "feat: add _reupload_fba orchestration"
```

---

## Task 6: `run_verify` main entry point

**Files:**
- Modify: `verify_tracking.py`
- Modify: `tests/test_verify_tracking.py`

- [ ] **Step 1: Write integration test for `run_verify`**

Append to `tests/test_verify_tracking.py`:

```python
from verify_tracking import run_verify


@pytest.mark.integration
def test_run_verify_returns_verify_result(browser_page, tmp_config, test_logger):
    """run_verify should return a VerifyResult for the given region."""
    region = {"name": "US", "amazon_url": "https://sellercentral.amazon.com"}
    shipments_all = {}  # empty — all found FBAs will land in not_in_sheet

    result = run_verify(browser_page, region, tmp_config, shipments_all)
    test_logger.info(
        f"run_verify result: checked={result.total_checked}, ok={result.total_ok}, "
        f"not_in_sheet={result.not_in_sheet[:3]}"
    )
    assert isinstance(result, VerifyResult)
    assert result.region == "US"
    assert result.total_checked >= 0
    assert result.total_ok <= result.total_checked
```

- [ ] **Step 2: Run test to confirm it fails**

```
pytest tests/test_verify_tracking.py -m integration -k "run_verify" -v
```

Expected: `ImportError: cannot import name 'run_verify'`

- [ ] **Step 3: Implement `run_verify` in `verify_tracking.py`**

Add after `_reupload_fba`:

```python
def run_verify(page, region: dict, config: dict, shipments_all: dict) -> VerifyResult:
    """
    Main entry point. Checks Amazon's shipping queue for this region,
    cross-references against shipments_all, re-uploads where possible.
    Returns a populated VerifyResult.
    """
    from run import wait_for_login

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
    from upload_tracking import _is_login_page, _wait_for_login as _upload_wait_login
    if _is_login_page(page):
        _upload_wait_login(page)

    # Apply Ready to ship filter
    if not _apply_ready_to_ship_filter(page):
        logger.warning(f"[{region_name}] Could not apply Ready to ship filter — skipping verify")
        return result

    # Collect all FBA IDs with missing tracking across all pages
    missing_fba_ids = _collect_all_missing_fba_ids(page)
    result.total_checked = len(missing_fba_ids)  # will be refined below
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

        if reup["status"] in ("success", "partial", "skipped"):
            if reup["total"] > 0 and reup["filled"] < reup["total"]:
                result.still_incomplete.append(reup)
            else:
                result.re_uploaded.append(reup)
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
```

- [ ] **Step 4: Run integration test**

```
pytest tests/test_verify_tracking.py -m integration -k "run_verify" -v
```

Expected: PASS (or SKIP if not logged in)

- [ ] **Step 5: Run all unit tests**

```
pytest tests/ -m unit -v
```

Expected: all unit tests PASS

- [ ] **Step 6: Commit**

```
git add verify_tracking.py tests/test_verify_tracking.py
git commit -m "feat: add run_verify main entry point"
```

---

## Task 7: `run.py` — `--verify` flag, auto-verify after upload, summary output

**Files:**
- Modify: `run.py`
- Modify: `tests/test_run_unit.py`

- [ ] **Step 1: Write unit tests for the new verify-summary path**

Append to `tests/test_run_unit.py`:

```python
from verify_tracking import VerifyResult, format_verify_summary


@pytest.mark.unit
def test_format_verify_summary_included_in_output(capsys):
    """format_verify_summary output should contain expected sections."""
    r = VerifyResult(region="US", total_checked=3, total_ok=2)
    r.not_in_sheet = ["FBA999"]
    output = format_verify_summary([r])
    assert "VERIFICATION" in output
    assert "FBA999" in output
    assert "Not in sheet" in output
```

- [ ] **Step 2: Run test to confirm it passes (it only tests the formatter, which already exists)**

```
pytest tests/test_run_unit.py -m unit -k "verify" -v
```

Expected: PASS

- [ ] **Step 3: Add `--verify` argument to `run.py` argparse**

In `run.py`, find the argparse block and add after `--regions`:

```python
parser.add_argument(
    "--verify",
    action="store_true",
    help="Check Amazon shipping queue for missing tracking IDs across all regions (standalone or auto-runs after --upload)",
)
```

- [ ] **Step 4: Add `run_verify` import inside `main()` in `run.py`**

Find the block that imports from project modules (around the `from parse_excel import ...` line) and add:

```python
from verify_tracking import run_verify, format_verify_summary
```

- [ ] **Step 5: Add standalone `--verify` mode to `run.py`**

In `run.py`, locate the `if args.check_only:` block. Add a new block **before** it (after `if args.discover_queue:` if present, otherwise after `if args.discover:`):

```python
if args.verify and not any([
    args.collect_only, args.check_only, args.from_json, args.discover,
]):
    # Standalone verify mode — no upload
    ts_verify = datetime.now().strftime("%Y%m%d_%H%M%S")
    verify_results = []
    for region in configured_regions:
        region_name = region["name"]
        amazon_url = region["amazon_url"]
        print(f"\n[{region_name}] Verify: logging in to {amazon_url}...")
        logged_in = wait_for_login(page, region_name, amazon_url, timeout_seconds=300)
        if not logged_in:
            print(f"[{region_name}] Login timed out — skipping verify for this region.")
            continue
        vr = run_verify(page, region, config, shipments_all)
        verify_results.append(vr)

    print(format_verify_summary(verify_results))
    return
```

- [ ] **Step 6: Add auto-verify after the upload loop in `run.py`**

In `run.py`, find the line `results = all_results` that comes after the region upload loop (around line 764). Directly after it, add:

```python
# Post-upload verification — check all regions for remaining missing tracking
verify_results = []
for region in configured_regions:
    region_name = region["name"]
    amazon_url = region["amazon_url"]
    print(f"\n[{region_name}] Running post-upload verification...")
    vr = run_verify(page, region, config, shipments_all)
    verify_results.append(vr)
print(format_verify_summary(verify_results))
```

- [ ] **Step 7: Run all unit tests**

```
pytest tests/ -m unit -v
```

Expected: all unit tests PASS

- [ ] **Step 8: Manual smoke test — standalone verify**

```
python run.py --verify --regions US
```

Expected: browser opens, navigates to queue, filters "Ready to ship", prints verify summary, exits cleanly.

- [ ] **Step 9: Manual smoke test — upload + auto-verify**

```
python run.py --check-only --regions US
```

Ensure existing modes still work, then run a real upload:

```
python run.py --regions US
```

Expected: upload completes, then verify pass runs automatically, combined summary printed.

- [ ] **Step 10: Run full test suite**

```
pytest tests/ -v
```

Expected: all unit tests PASS; integration tests PASS or SKIP (login-dependent)

- [ ] **Step 11: Commit**

```
git add run.py tests/test_run_unit.py
git commit -m "feat: add --verify flag and auto-verify after upload (v2.1)"
```

---

## Self-Review

**Spec coverage check:**
- Queue navigation + filter → Task 4 ✓
- All pages paginated → Task 4 `_collect_all_missing_fba_ids` ✓
- Cross-reference 4 buckets → Task 1 `_cross_reference` + Task 6 `run_verify` ✓
- Full carrier scrape on re-upload → Task 5 `_reupload_fba` ✓
- Skip-and-report for missing/not-in-sheet → Task 6 + Task 2 summary ✓
- All regions → Task 6 + Task 7 region loop ✓
- `--verify` standalone → Task 7 Step 5 ✓
- Auto-verify after upload → Task 7 Step 6 ✓
- Summary format with 4 buckets → Task 2 ✓
- Done cache updated for fully-filled → Task 6 ✓

**Placeholder scan:** None found.

**Type consistency:**
- `VerifyResult` defined in Task 1, imported in Tasks 2, 6, 7 ✓
- `_is_usable_tracking` defined Task 1, used Task 1 tests + Task 5 `_reupload_fba` ✓
- `_cross_reference` returns `{"reupload": list, "missing_in_sheet": list, "not_in_sheet": list}` — consumed identically in Task 6 ✓
- `_reupload_fba` returns `{"fba_id", "status", "filled", "total"}` — consumed identically in Task 6 ✓
- `format_verify_summary(results: list) -> str` — called in Tasks 7 Steps 5 and 6 ✓
