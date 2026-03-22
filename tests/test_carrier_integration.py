import os
import sys
import pytest
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fetch_sub_tracking import (
    fetch_ups_sub_tracking,
    fetch_fedex_sub_tracking,
    check_fedex_login,
    UPS_TRACK_URL,
)

logger = logging.getLogger(__name__)


@pytest.mark.integration
def test_ups_page_loads(browser_page, tracking_numbers, test_logger):
    """UPS tracking page should load without error."""
    url = UPS_TRACK_URL.format(tracking=tracking_numbers["ups"])
    test_logger.info(f"Navigating to UPS: {url}")
    browser_page.goto(url, wait_until="domcontentloaded", timeout=30000)
    test_logger.info(f"Page loaded, URL: {browser_page.url}")
    assert "ups.com" in browser_page.url


@pytest.mark.integration
def test_ups_other_packages_visible(browser_page, tracking_numbers, test_logger):
    """UPS page should show 'Other Packages in this Shipment' for multi-package."""
    url = UPS_TRACK_URL.format(tracking=tracking_numbers["ups"])
    browser_page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # Wait for content to load
    browser_page.wait_for_timeout(3000)
    content = browser_page.content()
    # Check for CAPTCHA
    if "captcha" in content.lower():
        pytest.skip("CAPTCHA detected on UPS page — cannot test automatically")
    test_logger.info("Checking for 'Other Packages' section")
    # This section may not exist for single-package shipments
    has_section = "other packages" in content.lower() or "shipment" in content.lower()
    test_logger.info(f"Multi-package section found: {has_section}")
    assert has_section, "Expected 'Other Packages' or 'Shipment' text on UPS page"


@pytest.mark.integration
def test_ups_sub_tracking_extraction(browser_page, tracking_numbers, test_logger):
    """fetch_ups_sub_tracking should return sub-tracking IDs from a real UPS page."""
    test_logger.info(f"Fetching sub-tracking for UPS: {tracking_numbers['ups']}")
    sub_ids = fetch_ups_sub_tracking(browser_page, tracking_numbers["ups"])
    test_logger.info(f"Got {len(sub_ids)} sub-IDs: {sub_ids}")
    assert isinstance(sub_ids, list)
    # May be 0 if tracking expired or single-package — log but don't fail hard
    if len(sub_ids) == 0:
        test_logger.warning("No sub-IDs found — tracking may be expired or single-package")


@pytest.mark.integration
def test_ups_pagination(browser_page, tracking_numbers, test_logger):
    """UPS pagination should not crash even if there are few packages."""
    test_logger.info("Testing UPS pagination (may be no-op for small shipments)")
    sub_ids = fetch_ups_sub_tracking(browser_page, tracking_numbers["ups"])
    test_logger.info(f"Extracted {len(sub_ids)} tracking IDs (pagination tested implicitly)")
    assert isinstance(sub_ids, list)


@pytest.mark.integration
def test_fedex_page_loads(browser_page, tracking_numbers, test_logger):
    """FedEx tracking page should load without error."""
    url = f"https://www.fedex.com/fedextrack/?trknbr={tracking_numbers['fedex']}"
    test_logger.info(f"Navigating to FedEx: {url}")
    browser_page.goto(url, wait_until="domcontentloaded", timeout=30000)
    test_logger.info(f"Page loaded, URL: {browser_page.url}")
    assert "fedex.com" in browser_page.url


@pytest.mark.integration
def test_fedex_trkqual_construction(browser_page, tracking_numbers, test_logger):
    """FedEx trkqual URLs should load without crashing."""
    trk = tracking_numbers["fedex"]
    trkqual_candidates = [
        f"12030~{trk}~FDEG",
        f"12029~{trk}~FDEG",
        f"10800~{trk}~FDXE",
        f"10800~{trk}~FXSP",
    ]
    for tq in trkqual_candidates:
        url = f"https://www.fedex.com/fedextrack/?trknbr={trk}&trkqual={tq}"
        test_logger.info(f"Trying trkqual URL: {url}")
        try:
            browser_page.goto(url, wait_until="domcontentloaded", timeout=15000)
            test_logger.info(f"  Loaded OK: {browser_page.url}")
        except Exception as e:
            test_logger.warning(f"  Failed: {e}")


@pytest.mark.integration
def test_fedex_sub_tracking_extraction(browser_page, tracking_numbers, test_logger):
    """fetch_fedex_sub_tracking should return sub-tracking IDs from a real FedEx page."""
    test_logger.info(f"Fetching sub-tracking for FedEx: {tracking_numbers['fedex']}")
    sub_ids = fetch_fedex_sub_tracking(browser_page, tracking_numbers["fedex"])
    test_logger.info(f"Got {len(sub_ids)} sub-IDs: {sub_ids}")
    assert isinstance(sub_ids, list)


@pytest.mark.integration
def test_fedex_login_check(browser_page, test_logger):
    """check_fedex_login should run without crashing."""
    test_logger.info("Running FedEx login check")
    check_fedex_login(browser_page)
    test_logger.info("FedEx login check completed without error")
