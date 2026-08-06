import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fc_resolver import (
    FcMatch,
    FcResolutionResult,
    group_unmatched_by_fc,
    append_fc_code_to_file,
    merge_resolved_rows,
    format_fc_resolution_summary,
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
    assert "ITX3  # auto-added 2026-08-07, confirmed via FBA19K4G0NSQ" in content
    assert "BNA" in content
    assert "PHX" in content


def test_append_fc_code_to_file_is_idempotent(tmp_path):
    f = tmp_path / "us_fc_codes.txt"
    f.write_text("BNA\nITX3  # auto-added 2026-08-01, confirmed via FBA1\n")
    append_fc_code_to_file(str(f), "itx3", "FBA_NEW", today="2026-08-07")
    content = f.read_text()
    assert content.upper().count("ITX3") == 1


def test_append_fc_code_to_file_creates_file_if_missing(tmp_path):
    f = tmp_path / "new_region.txt"
    append_fc_code_to_file(str(f), "MQJ1", "FBA_X", today="2026-08-07")
    assert "MQJ1" in f.read_text()


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
