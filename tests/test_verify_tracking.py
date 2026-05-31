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
