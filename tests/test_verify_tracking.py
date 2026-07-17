import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from verify_tracking import (
    _is_usable_tracking, _cross_reference, VerifyResult, format_verify_summary,
    _extract_row_name_pairs,
)


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
    assert r.shipment_names == {}


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


@pytest.mark.unit
def test_format_verify_summary_not_in_sheet_with_name():
    r = VerifyResult(region="US", total_checked=1, total_ok=0)
    r.not_in_sheet = ["FBA004"]
    r.shipment_names = {"FBA004": "40CT Large ~ 272CT Pure - YHM1"}
    out = format_verify_summary([r])
    assert "FBA004" in out
    assert "40CT Large ~ 272CT Pure - YHM1" in out


@pytest.mark.unit
def test_format_verify_summary_without_names_omits_name():
    r = VerifyResult(region="US", total_checked=1, total_ok=0)
    r.not_in_sheet = ["FBA004"]
    out = format_verify_summary([r])
    line = next(l for l in out.splitlines() if "FBA004" in l)
    assert line.strip() == "FBA004"


@pytest.mark.unit
def test_format_verify_summary_re_uploaded_with_name_and_tracking_ids():
    r = VerifyResult(region="US", total_checked=1, total_ok=0)
    r.re_uploaded = [{"fba_id": "FBA001", "filled": 2, "total": 2, "tracking_ids": ["1Z999AA1", "1Z999AA2"]}]
    r.shipment_names = {"FBA001": "Test Shipment Name"}
    out = format_verify_summary([r])
    assert "FBA001" in out
    assert "Test Shipment Name" in out
    assert "1Z999AA1" in out
    assert "1Z999AA2" in out


@pytest.mark.unit
def test_extract_row_name_pairs_basic():
    class FakeElement:
        def __init__(self, text):
            self._text = text

        def inner_text(self):
            return self._text

    class FakeRow:
        def __init__(self, row_text, link_text=None):
            self._row_text = row_text
            self._link_text = link_text

        def inner_text(self):
            return self._row_text

        def query_selector(self, sel):
            if sel == "a" and self._link_text is not None:
                return FakeElement(self._link_text)
            return None

    class FakePage:
        def __init__(self, rows):
            self._rows = rows

        def query_selector_all(self, sel):
            return self._rows if sel == "tbody tr" else []

    rows = [
        FakeRow("... FBA19JB4NX21 ... Missing tracking ID", link_text="40CT Large - YHM1"),
        FakeRow("... FBA19J5079L6 ... Missing tracking ID", link_text="PL-LC-US-CH-26097 - SMF3"),
    ]
    names = _extract_row_name_pairs(FakePage(rows))
    assert names == {
        "FBA19JB4NX21": "40CT Large - YHM1",
        "FBA19J5079L6": "PL-LC-US-CH-26097 - SMF3",
    }


@pytest.mark.unit
def test_extract_row_name_pairs_skips_row_without_fba_id():
    class FakeRow:
        def inner_text(self):
            return "no shipment id here"

        def query_selector(self, sel):
            return None

    class FakePage:
        def query_selector_all(self, sel):
            return [FakeRow()]

    assert _extract_row_name_pairs(FakePage()) == {}


@pytest.mark.unit
def test_extract_row_name_pairs_skips_row_without_link():
    class FakeRow:
        def inner_text(self):
            return "FBA19JB4NX21 present but no link"

        def query_selector(self, sel):
            return None

    class FakePage:
        def query_selector_all(self, sel):
            return [FakeRow()]

    assert _extract_row_name_pairs(FakePage()) == {}


from verify_tracking import (
    _navigate_to_queue_page,
    _apply_ready_to_ship_filter,
    _collect_all_missing_fba_ids,
)

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
