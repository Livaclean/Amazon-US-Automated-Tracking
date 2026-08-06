import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fc_resolver import (
    FcMatch,
    FcResolutionResult,
    group_unmatched_by_fc,
    append_fc_code_to_file,
    probe_fc_codes,
    merge_resolved_rows,
    format_fc_resolution_summary,
    _dedupe_fba_ids,
)


def test_group_unmatched_by_fc_groups_and_uppercases():
    rows = [
        {"fc_code": "itx3", "fba_id": "FBA1"},
        {"fc_code": "ITX3", "fba_id": "FBA2"},
        {"fc_code": "mqj1", "fba_id": "FBA3"},
        {"fc_code": "", "fba_id": "FBA4"},
    ]
    grouped = group_unmatched_by_fc(rows)
    assert set(grouped.keys()) == {"ITX3", "MQJ1"}
    assert [r["fba_id"] for r in grouped["ITX3"]] == ["FBA1", "FBA2"]


def test_append_fc_code_to_file_adds_new_code(tmp_path):
    f = tmp_path / "us_fc_codes.txt"
    f.write_text("BNA\nPHX\n")
    append_fc_code_to_file(str(f), "ITX3", "FBA19K4G0NSQ", today="2026-08-07")
    content = f.read_text()
    assert "# auto-added 2026-08-07, confirmed via FBA19K4G0NSQ\nITX3" in content
    assert "BNA" in content
    assert "PHX" in content


def test_append_fc_code_to_file_is_idempotent(tmp_path):
    f = tmp_path / "us_fc_codes.txt"
    f.write_text("BNA\n# auto-added 2026-08-01, confirmed via FBA1\nITX3\n")
    append_fc_code_to_file(str(f), "itx3", "FBA_NEW", today="2026-08-07")
    content = f.read_text()
    assert content.upper().count("ITX3") == 1


def test_append_fc_code_to_file_creates_file_if_missing(tmp_path):
    f = tmp_path / "new_region.txt"
    append_fc_code_to_file(str(f), "MQJ1", "FBA_X", today="2026-08-07")
    assert "MQJ1" in f.read_text()


def test_append_fc_code_to_file_round_trips_through_load_fc_prefixes(tmp_path):
    """The auto-added format must still be recognized by parse_excel's own matcher —
    a comment on the same line as the code would corrupt the stored prefix, since
    load_fc_prefixes() only skips lines that START with '#'; it doesn't strip
    trailing inline comments."""
    from parse_excel import load_fc_prefixes, is_region_fc
    f = tmp_path / "us_fc_codes.txt"
    f.write_text("BNA\n")
    append_fc_code_to_file(str(f), "ITX3", "FBA19K4G0NSQ", today="2026-08-07")
    prefixes = load_fc_prefixes(str(f))
    assert is_region_fc("ITX3XXXX", prefixes)


def test_merge_resolved_rows_adds_shipments_to_correct_region():
    all_regions_data = {
        "US": {},
        "CA": {"FBA_EXISTING": [{"tracking": "1Z1", "carrier": "UPS", "row_number": 2}]},
    }
    unresolved_by_fc = {
        "ITX3": [
            {"fc_code": "ITX3", "fba_id": "FBA1", "tracking_num": "1Z999", "carrier": "UPS", "row_number": 10},
        ],
    }
    resolved = [FcMatch(fc_code="ITX3", region="US", probe_fba_id="FBA1", fba_ids=["FBA1"])]

    merged = merge_resolved_rows(resolved, unresolved_by_fc, all_regions_data)

    assert "FBA1" in merged["US"]
    assert merged["US"]["FBA1"][0]["tracking"] == "1Z999"
    assert "FBA_EXISTING" in merged["CA"]  # untouched


def test_format_fc_resolution_summary_shows_uploaded_count():
    result = FcResolutionResult(
        resolved=[FcMatch(fc_code="ITX3", region="US", probe_fba_id="FBA1", fba_ids=["FBA1", "FBA2"])],
        unresolved=[{"fc_code": "XYZ9", "fba_ids": ["FBA9"]}],
    )
    upload_results = [
        {"fba_id": "FBA1", "status": "success"},
        {"fba_id": "FBA2", "status": "failed"},
    ]
    text = format_fc_resolution_summary(result, upload_results)
    assert "ITX3 -> US (confirmed via FBA1) - 1 shipment(s) uploaded" in text
    assert "XYZ9 - FBA9" in text


def test_format_fc_resolution_summary_empty_when_nothing_to_report():
    assert format_fc_resolution_summary(FcResolutionResult(), []) == ""


def test_probe_fc_codes_matches_first_successful_region():
    regions = [
        {"name": "US", "amazon_url": "https://us.example"},
        {"name": "CA", "amazon_url": "https://ca.example"},
    ]
    unresolved_by_fc = {
        "ITX3": [
            {"fc_code": "ITX3", "fba_id": "FBA1"},
            {"fc_code": "ITX3", "fba_id": "FBA2"},
        ],
    }

    def fake_login(page, region_name, amazon_url, timeout_seconds=300):
        return True

    def fake_navigate(page, fba_id, base_url):
        return base_url == "https://ca.example"  # only CA "has" this shipment

    result = probe_fc_codes(None, unresolved_by_fc, regions, fake_login, fake_navigate)

    assert len(result.resolved) == 1
    assert result.resolved[0].fc_code == "ITX3"
    assert result.resolved[0].region == "CA"
    assert result.resolved[0].fba_ids == ["FBA1", "FBA2"]
    assert result.unresolved == []


def test_probe_fc_codes_reports_unresolved_when_no_region_matches():
    regions = [{"name": "US", "amazon_url": "https://us.example"}]
    unresolved_by_fc = {"XYZ9": [{"fc_code": "XYZ9", "fba_id": "FBA9"}]}

    result = probe_fc_codes(None, unresolved_by_fc, regions, lambda *a, **k: True, lambda *a, **k: False)

    assert result.resolved == []
    assert result.unresolved == [{"fc_code": "XYZ9", "fba_ids": ["FBA9"]}]


def test_probe_fc_codes_skips_region_when_login_fails():
    regions = [
        {"name": "US", "amazon_url": "https://us.example"},
        {"name": "CA", "amazon_url": "https://ca.example"},
    ]
    unresolved_by_fc = {"ITX3": [{"fc_code": "ITX3", "fba_id": "FBA1"}]}

    def fake_login(page, region_name, amazon_url, timeout_seconds=300):
        return region_name == "CA"  # US login fails, CA succeeds

    def fake_navigate(page, fba_id, base_url):
        return True  # would match whichever region actually gets probed

    result = probe_fc_codes(None, unresolved_by_fc, regions, fake_login, fake_navigate)

    assert len(result.resolved) == 1
    assert result.resolved[0].region == "CA"  # US was skipped due to failed login


def test_probe_fc_codes_resolves_star_prefix_to_awd_without_probing():
    """AWD (STAR- prefix) shipments must resolve directly to the AWD region without
    probing at all — even when a region sharing the same amazon_url (e.g. US) would
    also return True if it were probed, since navigate_to_shipment routes STAR- IDs
    to the AWD URL pattern regardless of which base_url is passed."""
    regions = [
        {"name": "US", "amazon_url": "https://sellercentral.amazon.com"},
        {"name": "AWD", "amazon_url": "https://sellercentral.amazon.com"},
    ]
    unresolved_by_fc = {
        "STAR9": [{"fc_code": "STAR9", "fba_id": "STAR-ABC123"}],
    }

    def fake_login(page, region_name, amazon_url, timeout_seconds=60):
        raise AssertionError(f"login should not be attempted for AWD FC codes, got region={region_name}")

    def fake_navigate(page, fba_id, base_url):
        raise AssertionError("navigate_fn should not be called for STAR- FBA IDs")

    result = probe_fc_codes(None, unresolved_by_fc, regions, fake_login, fake_navigate)

    assert len(result.resolved) == 1
    assert result.resolved[0].fc_code == "STAR9"
    assert result.resolved[0].region == "AWD"
    assert result.resolved[0].fba_ids == ["STAR-ABC123"]
    assert result.unresolved == []


def test_probe_fc_codes_leaves_ambiguous_shared_url_match_unresolved():
    """A non-AWD FC code that matches under a region whose amazon_url is shared by
    another configured region is genuinely ambiguous — navigate_fn can't tell EU and
    FR apart when they use the same domain, so it must be left unresolved rather than
    silently attributed to whichever region is tried first."""
    regions = [
        {"name": "EU", "amazon_url": "https://shared.example"},
        {"name": "FR", "amazon_url": "https://shared.example"},
    ]
    unresolved_by_fc = {
        "MQJ1": [{"fc_code": "MQJ1", "fba_id": "FBA1"}],
    }

    def fake_login(page, region_name, amazon_url, timeout_seconds=60):
        return True

    def fake_navigate(page, fba_id, base_url):
        return base_url == "https://shared.example"  # matches for both EU and FR

    result = probe_fc_codes(None, unresolved_by_fc, regions, fake_login, fake_navigate)

    assert result.resolved == []
    assert result.unresolved == [{"fc_code": "MQJ1", "fba_ids": ["FBA1"]}]


def test_probe_fc_codes_default_login_timeout_is_60_seconds():
    """Confirms the reduced default login timeout (was hardcoded 300s) is actually
    threaded through to wait_for_login_fn when the caller doesn't override it."""
    regions = [{"name": "US", "amazon_url": "https://us.example"}]
    unresolved_by_fc = {"ITX3": [{"fc_code": "ITX3", "fba_id": "FBA1"}]}
    seen_timeouts = []

    def fake_login(page, region_name, amazon_url, timeout_seconds=300):
        seen_timeouts.append(timeout_seconds)
        return True

    probe_fc_codes(None, unresolved_by_fc, regions, fake_login, lambda *a, **k: True)

    assert seen_timeouts == [60]


def test_dedupe_fba_ids_splits_combined_ids_drops_wfa_and_dedupes():
    rows = [
        {"fba_id": "STAR-A/STAR-B"},
        {"fba_id": "STAR-A"},  # duplicate of a part already seen
        {"fba_id": "WMT123WFA"},  # dropped: Walmart ID
        {"fba_id": "  STAR-C  "},  # whitespace trimmed
        {"fba_id": ""},  # empty, ignored
    ]
    assert _dedupe_fba_ids(rows) == ["STAR-A", "STAR-B", "STAR-C"]


def test_probe_fc_codes_uses_dedupe_for_resolved_fba_ids():
    """A resolved FcMatch's fba_ids should be deduped/split via _dedupe_fba_ids rather
    than one raw entry per Excel row, so combined IDs and duplicate carton rows don't
    inflate the eventual 'N shipment(s) uploaded' summary count."""
    regions = [{"name": "US", "amazon_url": "https://us.example"}]
    unresolved_by_fc = {
        "ITX3": [
            {"fc_code": "ITX3", "fba_id": "STAR-A/STAR-B"},
            {"fc_code": "ITX3", "fba_id": "STAR-A"},
            {"fc_code": "ITX3", "fba_id": "WMT9WFA"},
        ],
    }

    result = probe_fc_codes(None, unresolved_by_fc, regions, lambda *a, **k: True, lambda *a, **k: True)

    assert len(result.resolved) == 1
    assert result.resolved[0].fba_ids == ["STAR-A", "STAR-B"]


def test_merge_resolved_rows_extends_rather_than_replaces_same_fba_id():
    """Two resolved matches touching the same FBA ID/region should have their tracking
    entries accumulate, not have the second overwrite the first."""
    all_regions_data = {"US": {}}
    unresolved_by_fc = {
        "ITX3": [
            {"fc_code": "ITX3", "fba_id": "FBA1", "tracking_num": "1Z111", "carrier": "UPS", "row_number": 1},
        ],
        "MQJ1": [
            {"fc_code": "MQJ1", "fba_id": "FBA1", "tracking_num": "1Z222", "carrier": "UPS", "row_number": 2},
        ],
    }
    resolved = [
        FcMatch(fc_code="ITX3", region="US", probe_fba_id="FBA1", fba_ids=["FBA1"]),
        FcMatch(fc_code="MQJ1", region="US", probe_fba_id="FBA1", fba_ids=["FBA1"]),
    ]

    merged = merge_resolved_rows(resolved, unresolved_by_fc, all_regions_data)

    trackings = {e["tracking"] for e in merged["US"]["FBA1"]}
    assert trackings == {"1Z111", "1Z222"}
    assert len(merged["US"]["FBA1"]) == 2


def test_append_fc_code_to_file_rejects_empty_code(tmp_path):
    f = tmp_path / "us_fc_codes.txt"
    f.write_text("BNA\n")
    append_fc_code_to_file(str(f), "", "FBA1", today="2026-08-07")
    content = f.read_text()
    assert content == "BNA\n"  # unchanged — nothing written


def test_append_fc_code_to_file_rejects_code_with_internal_whitespace(tmp_path):
    f = tmp_path / "us_fc_codes.txt"
    f.write_text("BNA\n")
    append_fc_code_to_file(str(f), "ITX3 garbage", "FBA1", today="2026-08-07")
    content = f.read_text()
    assert content == "BNA\n"  # unchanged — nothing written
