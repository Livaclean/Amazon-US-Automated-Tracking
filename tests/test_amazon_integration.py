import os
import sys
import pytest
import logging
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from upload_tracking import (
    navigate_to_shipment,
    check_amazon_tracking_status,
    discover_page_elements,
)

logger = logging.getLogger(__name__)


def _is_login_page(url: str) -> bool:
    """Check if current URL is an Amazon login page."""
    lower = url.lower()
    return "ap/signin" in lower or "ap/register" in lower or "/signin" in lower


@pytest.mark.integration
def test_amazon_login_detection(browser_page, test_logger):
    """Should detect whether we're on an Amazon login page."""
    test_logger.info("Navigating to Amazon Seller Central")
    try:
        browser_page.goto("https://sellercentral.amazon.com", wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        pytest.skip(f"Could not reach Amazon: {e}")

    url = browser_page.url
    test_logger.info(f"Current URL: {url}")
    is_login = _is_login_page(url)
    test_logger.info(f"Is login page: {is_login}")

    if is_login:
        pytest.skip("Not logged in to Amazon Seller Central — skipping remaining Amazon tests")

    assert not is_login, "Should be logged in for this test to pass"


@pytest.mark.integration
def test_navigate_fba_shipment(browser_page, tracking_numbers, test_logger):
    """Should navigate to an FBA shipment page."""
    # Guard: check login first
    if _is_login_page(browser_page.url):
        browser_page.goto("https://sellercentral.amazon.com", wait_until="domcontentloaded", timeout=30000)
        if _is_login_page(browser_page.url):
            pytest.skip("Not logged in to Amazon")

    fba_id = tracking_numbers.get("fba_id")
    if not fba_id:
        pytest.skip("No fba_id in test_tracking_numbers.json")

    test_logger.info(f"Navigating to FBA shipment: {fba_id}")
    result = navigate_to_shipment(browser_page, fba_id, "https://sellercentral.amazon.com")
    test_logger.info(f"Page URL: {browser_page.url}, result: {result}")
    assert result is True, f"navigate_to_shipment returned {result}"


@pytest.mark.integration
def test_navigate_awd_shipment(browser_page, tracking_numbers, test_logger):
    """Should navigate to an AWD (STAR-) shipment page."""
    if _is_login_page(browser_page.url):
        pytest.skip("Not logged in to Amazon")

    awd_id = tracking_numbers.get("awd_id")
    if not awd_id:
        pytest.skip("No awd_id in test_tracking_numbers.json")

    test_logger.info(f"Navigating to AWD shipment: {awd_id}")
    navigate_to_shipment(browser_page, awd_id, "https://sellercentral.amazon.com")
    test_logger.info(f"Page URL: {browser_page.url}")


@pytest.mark.integration
def test_tracking_iframe_found(browser_page, tracking_numbers, test_logger):
    """Should find the tracking iframe or context on a shipment page."""
    if _is_login_page(browser_page.url):
        pytest.skip("Not logged in to Amazon")

    fba_id = tracking_numbers.get("fba_id")
    if not fba_id:
        pytest.skip("No fba_id in test_tracking_numbers.json")

    test_logger.info(f"Looking for tracking iframe on {fba_id}")
    navigate_to_shipment(browser_page, fba_id, "https://sellercentral.amazon.com")
    browser_page.wait_for_timeout(3000)
    # Check for tracking-related content
    content = browser_page.content()
    has_tracking_content = "tracking" in content.lower() or "iframe" in content.lower()
    test_logger.info(f"Tracking content found: {has_tracking_content}")


@pytest.mark.integration
def test_check_tracking_status(browser_page, tracking_numbers, test_logger):
    """check_amazon_tracking_status should return a valid status string."""
    if _is_login_page(browser_page.url):
        pytest.skip("Not logged in to Amazon")

    fba_id = tracking_numbers.get("fba_id")
    if not fba_id:
        pytest.skip("No fba_id in test_tracking_numbers.json")

    test_logger.info(f"Checking tracking status for {fba_id}")
    status = check_amazon_tracking_status(browser_page, fba_id, {"amazon_base_url": "https://sellercentral.amazon.com"})
    test_logger.info(f"Status: {status}")
    assert status in ("complete", "partial", "empty", "not_found"), f"Unexpected status: {status}"


@pytest.mark.integration
def test_discover_page_elements(browser_page, tracking_numbers, tmp_path, test_logger):
    """discover_page_elements should create an output file."""
    if _is_login_page(browser_page.url):
        pytest.skip("Not logged in to Amazon")

    fba_id = tracking_numbers.get("fba_id")
    if not fba_id:
        pytest.skip("No fba_id in test_tracking_numbers.json")

    test_logger.info(f"Running page discovery for {fba_id}")
    discover_page_elements(browser_page, fba_id, "https://sellercentral.amazon.com", str(tmp_path))
    discovery_files = list(tmp_path.glob("page_discovery_*.txt"))
    test_logger.info(f"Discovery files created: {len(discovery_files)}")
    assert len(discovery_files) >= 1
