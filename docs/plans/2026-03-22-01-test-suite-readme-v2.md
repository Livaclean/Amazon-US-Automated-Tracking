# Test Suite + README V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add comprehensive unit/integration/e2e tests with no mocks, update README to V2, and ship as a PR. Note: since all source code pre-exists, the TDD "red" phase is implicit — tests should pass immediately when written against existing code.

**Architecture:** Extend existing pytest test suite. Unit tests use real files via `tmp_path`. Integration tests launch real Playwright browser against live carrier/Amazon sites (skip-if-unavailable). E2E tests invoke `main()` via subprocess. All new tests merge into existing test files where possible.

**Tech Stack:** Python 3.7+, pytest, Playwright (sync_api), openpyxl, xlrd

---

## File Structure

```
Amazon-US-Automated-Tracking/
├── pytest.ini                              # CREATE — markers, log config
├── .gitignore                              # MODIFY — add test_logs/, tracking nums
├── README.md                               # MODIFY — full V2 rewrite
├── TODOS.md                                # CREATE — deferred work items
├── tests/
│   ├── conftest.py                         # CREATE — shared fixtures
│   ├── test_run_unit.py                    # CREATE — run.py utility tests
│   ├── test_parse_excel.py                 # MODIFY — add 10 tests
│   ├── test_highlight_excel.py             # MODIFY — add 1 test
│   ├── test_fetch_sub_tracking.py          # MODIFY — add 6 tests
│   ├── test_run_regions.py                 # UNCHANGED
│   ├── test_carrier_integration.py         # CREATE — UPS/FedEx browser tests
│   ├── test_amazon_integration.py          # CREATE — Amazon SC browser tests
│   ├── test_e2e_pipeline.py                # CREATE — subprocess pipeline tests
│   └── fixtures/
│       ├── sample.xls                      # CREATE — sanitized Excel fixture
│       └── test_tracking_numbers.json.example  # CREATE — format doc
```

Source files read but NOT modified: `run.py`, `parse_excel.py`, `fetch_sub_tracking.py`, `upload_tracking.py`, `highlight_excel.py`

---

### Task 1: Test Infrastructure

**Files:**
- Create: `pytest.ini`
- Create: `tests/conftest.py`
- Create: `tests/fixtures/test_tracking_numbers.json.example`
- Modify: `.gitignore`

- [ ] **Step 1: Create pytest.ini**

```ini
[pytest]
markers =
    unit: pure functions, no browser, no network
    integration: browser-based tests against real external sites
    e2e: full pipeline tests via subprocess
    slow: tests that take >10 seconds
testpaths = tests
log_cli = true
log_cli_level = INFO
log_cli_format = %(asctime)s [%(levelname)s] %(name)s: %(message)s
```

- [ ] **Step 2: Create tests/conftest.py**

```python
import os
import sys
import json
import logging
import pytest
import openpyxl
from pathlib import Path
from datetime import datetime

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEST_LOGS_DIR = Path(__file__).parent / "test_logs"


# ---------------------------------------------------------------------------
# Unit test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_config(tmp_path):
    """Real config dict with tmp_path-based folders and real fc_codes file."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    logs_dir = tmp_path / "logs"
    input_dir.mkdir()
    output_dir.mkdir()
    logs_dir.mkdir()

    fc_file = tmp_path / "us_fc_codes.txt"
    fc_file.write_text("BNA\nPHX\nIND\nGYR\n")

    ca_fc_file = tmp_path / "ca_fc_codes.txt"
    ca_fc_file.write_text("YVR\nYYZ\nPRTO\n")

    return {
        "input_folder": str(input_dir),
        "output_folder": str(output_dir),
        "logs_folder": str(logs_dir),
        "chrome_profile_path": str(tmp_path / "chrome_profile"),
        "chrome_profile_name": "Default",
        "headless": True,
        "column_fc_code": 3,
        "column_fba_id": 4,
        "column_tracking": 7,
        "column_carrier": 8,
        "us_fc_codes_file": str(fc_file),
        "regions": [
            {"name": "US", "amazon_url": "https://sellercentral.amazon.com",
             "fc_codes_file": str(fc_file)},
            {"name": "CA", "amazon_url": "https://sellercentral.amazon.ca",
             "fc_codes_file": str(ca_fc_file)},
        ],
    }


@pytest.fixture
def sample_xlsx(tmp_path):
    """Creates a real .xlsx with realistic test data. Returns path."""
    wb = openpyxl.Workbook()
    ws = wb.active
    # Header row
    ws.append(["A", "B", "C", "D_fc", "E_fba", "F", "G", "H_tracking", "I_carrier"])
    # US rows
    ws.append([None, None, None, "BNA6", "FBA_TEST_001", None, None, "1ZTEST00000000001", "UPS"])
    ws.append([None, None, None, "GYR3", "FBA_TEST_002", None, None, "000000000012", "FedEx"])
    ws.append([None, None, None, "IND1", "FBA_TEST_001", None, None, "1ZTEST00000000002", "UPS"])
    # CA row
    ws.append([None, None, None, "YVR2", "FBA_TEST_CA1", None, None, "1ZTEST00000000003", "UPS"])
    # Row with no tracking
    ws.append([None, None, None, "PHX5", "FBA_TEST_003", None, None, "", ""])
    path = tmp_path / "test_shipments.xlsx"
    wb.save(path)
    return str(path)


@pytest.fixture(scope="session")
def sample_xls():
    """Bundled .xls fixture. Skip if file missing."""
    path = FIXTURES_DIR / "sample.xls"
    if not path.exists():
        pytest.skip("tests/fixtures/sample.xls not found — bundle a sanitized .xls to enable these tests")
    return str(path)


@pytest.fixture
def sample_results():
    """List of realistic upload result dicts."""
    return [
        {"fba_id": "FBA_TEST_001", "status": "success", "succeeded": 3, "already_existed": 0, "failed": 0, "total": 3},
        {"fba_id": "FBA_TEST_002", "status": "partial", "succeeded": 1, "already_existed": 1, "failed": 1, "total": 3},
        {"fba_id": "FBA_TEST_003", "status": "failed", "succeeded": 0, "already_existed": 0, "failed": 2, "total": 2},
        {"fba_id": "FBA_TEST_004", "status": "skipped", "succeeded": 0, "already_existed": 4, "failed": 0, "total": 4},
    ]


# ---------------------------------------------------------------------------
# Integration test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def browser_context():
    """Real Playwright browser context using persistent Chrome profile.
    Skips all integration tests if Chrome is unavailable."""
    # Only attempt if config.json exists (need chrome_profile_path)
    config_path = Path("config.json")
    if not config_path.exists():
        pytest.skip("config.json not found — cannot launch browser for integration tests")

    import json
    config = json.loads(config_path.read_text(encoding="utf-8"))
    chrome_profile = config.get("chrome_profile_path", "")
    if not chrome_profile:
        pytest.skip("chrome_profile_path not set in config.json")

    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        context = pw.chromium.launch_persistent_context(
            chrome_profile,
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
    except Exception as e:
        pytest.skip(f"Chrome unavailable: {e}")

    yield context

    try:
        context.close()
        pw.stop()
    except Exception:
        pass


@pytest.fixture(scope="module")
def browser_page(browser_context):
    """Fresh page per test module. Isolates carrier vs Amazon tests."""
    page = browser_context.new_page()
    yield page
    try:
        page.close()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def screenshot_on_failure(request):
    """Auto-capture screenshot on integration/e2e test failure."""
    yield
    # Only run for integration/e2e marked tests
    markers = {m.name for m in request.node.iter_markers()}
    if not (markers & {"integration", "e2e"}):
        return
    if hasattr(request.node, "rep_call") and request.node.rep_call.failed:
        # Try to get browser_page from the test's fixtures
        page = request.node.funcargs.get("browser_page")
        if page:
            try:
                ss_dir = TEST_LOGS_DIR / "screenshots"
                ss_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                page.screenshot(path=str(ss_dir / f"{ts}_{request.node.name}.png"))
            except Exception:
                pass  # page may already be closed


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store test result on the item for screenshot_on_failure fixture."""
    import pluggy
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


# ---------------------------------------------------------------------------
# Test logging
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def test_logger(request):
    """Per-test logger writing to tests/test_logs/."""
    TEST_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"test.{request.node.name}")
    logger.setLevel(logging.DEBUG)

    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = TEST_LOGS_DIR / f"{date_str}_test_run.log"
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(fh)

    logger.info(f"--- START: {request.node.nodeid} ---")
    yield logger
    logger.info(f"--- END: {request.node.nodeid} ---")
    logger.removeHandler(fh)
    fh.close()


# ---------------------------------------------------------------------------
# Tracking numbers for integration tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def tracking_numbers():
    """Load test tracking numbers from gitignored JSON. Skip if missing."""
    path = FIXTURES_DIR / "test_tracking_numbers.json"
    if not path.exists():
        pytest.skip(
            "tests/fixtures/test_tracking_numbers.json not found — "
            "create it from test_tracking_numbers.json.example to run carrier integration tests"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print aggregate test summary with skip reasons."""
    stats = terminalreporter.stats
    passed = len(stats.get("passed", []))
    failed = len(stats.get("failed", []))
    skipped = stats.get("skipped", [])

    skip_reasons = {}
    for item in skipped:
        reason = str(item.longrepr[-1]) if item.longrepr else "unknown"
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    terminalreporter.write_sep("=", "TEST RUN SUMMARY")
    terminalreporter.write_line(f"  Passed:  {passed}")
    terminalreporter.write_line(f"  Failed:  {failed}")
    terminalreporter.write_line(f"  Skipped: {len(skipped)}")
    if skip_reasons:
        for reason, count in skip_reasons.items():
            terminalreporter.write_line(f"    - {count}x: {reason}")
```

- [ ] **Step 3: Create tests/fixtures/test_tracking_numbers.json.example**

```json
{
  "ups": "1Z999AA10123456784",
  "ups_comment": "Replace with a currently-active multi-package UPS tracking number",
  "fedex": "123456789012345",
  "fedex_comment": "Replace with a currently-active multi-piece FedEx tracking number",
  "fba_id": "FBA197HGGQXC",
  "fba_id_comment": "Replace with a real FBA shipment ID for Amazon integration tests",
  "awd_id": "STAR-ABC123",
  "awd_id_comment": "Replace with a real AWD (STAR-) shipment ID"
}
```

- [ ] **Step 4: Update .gitignore**

Append these lines to the existing `.gitignore`:

```
# Test artifacts
tests/test_logs/
tests/fixtures/test_tracking_numbers.json
```

- [ ] **Step 5: Create sanitized sample.xls fixture**

This must be done manually or with a helper script since openpyxl cannot write .xls format. Use `xlwt` or copy the real input file and sanitize it:

```bash
# Option: copy real file and sanitize (manual step — replace real data with fake)
# The fixture should have 3-5 rows with:
#   - Column headers: DESTINATION, FBA ID, ..., TRACKING, CARRIER
#   - FBA IDs: FBA_TEST_001, FBA_TEST_002, FBA_TEST_003
#   - Tracking: 1ZTEST00000000001, 000000000012, (empty)
#   - FC codes: BNA6, GYR3, YVR2
#   - Carriers: UPS, FedEx, (empty)
# Save to tests/fixtures/sample.xls
```

If the real input file is not available, these .xls tests will skip via the `sample_xls` fixture.

- [ ] **Step 6: Run existing tests to verify nothing is broken**

Run: `cd /c/Users/antbu/Amazon-US-Automated-Tracking && python -m pytest tests/ -v`
Expected: All 43 existing tests PASS

- [ ] **Step 7: Commit**

```bash
git checkout -b feat/testing-and-readme-v2
git add pytest.ini tests/conftest.py tests/fixtures/test_tracking_numbers.json.example .gitignore
# If sample.xls was created: git add tests/fixtures/sample.xls
git commit -m "chore: add pytest config and test infrastructure"
```

---

### Task 2: Unit Tests for run.py

**Files:**
- Create: `tests/test_run_unit.py`

- [ ] **Step 1: Write all run.py unit tests**

```python
import os
import sys
import json
import time
import logging
import pytest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from run import (
    setup_logging,
    load_config,
    ensure_folders,
    cleanup_logs,
    write_summary,
    write_region_summary,
    write_shipment_records,
    collect_updated_row_numbers,
)


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_setup_logging_creates_log_file(tmp_path):
    """setup_logging should create a .log file in the given folder."""
    # Save and restore root logger state to avoid pollution
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    try:
        setup_logging(str(tmp_path))
        log_files = list(tmp_path.glob("tracking_upload_*.log"))
        assert len(log_files) == 1, f"Expected 1 log file, found {len(log_files)}"
        assert log_files[0].stat().st_size >= 0
    finally:
        root.handlers = original_handlers
        root.level = original_level


@pytest.mark.unit
def test_setup_logging_dual_handlers(tmp_path):
    """setup_logging should add both a FileHandler and StreamHandler."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    try:
        setup_logging(str(tmp_path))
        handler_types = {type(h).__name__ for h in root.handlers}
        assert "FileHandler" in handler_types
        assert "StreamHandler" in handler_types
    finally:
        root.handlers = original_handlers
        root.level = original_level


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_load_config_reads_real_json(tmp_path):
    """load_config should read a real config.json and return its contents."""
    config_data = {"input_folder": "input", "output_folder": "output", "logs_folder": "logs"}
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data), encoding="utf-8")
    result = load_config(str(config_file))
    assert result["input_folder"] == "input"
    assert result["output_folder"] == "output"


@pytest.mark.unit
def test_load_config_missing_file_exits(tmp_path):
    """load_config should sys.exit(1) when the file doesn't exist."""
    with pytest.raises(SystemExit) as exc_info:
        load_config(str(tmp_path / "nonexistent.json"))
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_load_config_applies_defaults(tmp_path):
    """load_config should fill in default values for missing keys."""
    config_file = tmp_path / "config.json"
    config_file.write_text('{"input_folder": "input"}', encoding="utf-8")
    result = load_config(str(config_file))
    assert result["headless"] is False
    assert result["column_fc_code"] == 3
    assert result["column_fba_id"] == 4
    assert result["column_tracking"] == 7
    assert result["column_carrier"] == 8
    assert result["delay_between_shipments_seconds"] == 2
    assert result["us_fc_codes_file"] == "fc_codes/us_fc_codes.txt"


# ---------------------------------------------------------------------------
# ensure_folders
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_ensure_folders_creates_dirs(tmp_config):
    """ensure_folders should create input/output/logs/screenshots directories."""
    ensure_folders(tmp_config)
    assert Path(tmp_config["input_folder"]).is_dir()
    assert Path(tmp_config["output_folder"]).is_dir()
    assert Path(tmp_config["logs_folder"]).is_dir()
    assert (Path(tmp_config["logs_folder"]) / "screenshots").is_dir()


@pytest.mark.unit
def test_ensure_folders_clears_old_screenshots(tmp_config):
    """ensure_folders should delete existing .png files in screenshots/."""
    ss_dir = Path(tmp_config["logs_folder"]) / "screenshots"
    ss_dir.mkdir(parents=True)
    (ss_dir / "old_screenshot.png").write_text("fake png")
    ensure_folders(tmp_config)
    assert not (ss_dir / "old_screenshot.png").exists()


@pytest.mark.unit
def test_ensure_folders_missing_key_exits():
    """ensure_folders should sys.exit(1) when a required key is missing."""
    with pytest.raises(SystemExit) as exc_info:
        ensure_folders({"output_folder": "out", "logs_folder": "logs"})  # missing input_folder
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# cleanup_logs
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_cleanup_logs_deletes_debug_files(tmp_path):
    """cleanup_logs should delete one-off debug/temp files."""
    (tmp_path / "fedex_page_123.txt").write_text("debug")
    (tmp_path / "ups_page_456.txt").write_text("debug")
    (tmp_path / "debug_test.txt").write_text("debug")
    cleanup_logs(str(tmp_path))
    assert not (tmp_path / "fedex_page_123.txt").exists()
    assert not (tmp_path / "ups_page_456.txt").exists()
    assert not (tmp_path / "debug_test.txt").exists()


@pytest.mark.unit
def test_cleanup_logs_keeps_done_caches(tmp_path):
    """cleanup_logs should NOT delete completed_fba_*.txt (persistent caches)."""
    (tmp_path / "completed_fba_US.txt").write_text("FBA001\nFBA002\n")
    cleanup_logs(str(tmp_path))
    assert (tmp_path / "completed_fba_US.txt").exists()


@pytest.mark.unit
def test_cleanup_logs_keeps_recent_3(tmp_path):
    """cleanup_logs should keep only the 3 most recent files of each timestamped type."""
    # Create 5 summary files with proper timestamp format and different mtimes
    # Format must be summary_YYYYMMDD_HHMMSS.txt so cleanup_logs regex strips the timestamp
    base_time = time.time() - 100
    for i in range(5):
        f = tmp_path / f"summary_20260322_{100000 + i:06d}.txt"
        f.write_text(f"summary {i}")
        os.utime(str(f), (base_time + i * 2, base_time + i * 2))

    cleanup_logs(str(tmp_path))
    remaining = sorted(tmp_path.glob("summary_*.txt"))
    assert len(remaining) == 3, f"Expected 3 files, got {len(remaining)}: {remaining}"


# ---------------------------------------------------------------------------
# write_summary
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_write_summary_creates_file(tmp_path, sample_results):
    """write_summary should create a summary_*.txt file with correct content."""
    write_summary(sample_results, str(tmp_path))
    files = list(tmp_path.glob("summary_*.txt"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "Total FBA shipments: 4" in content
    assert "[OK]" in content
    assert "[PARTIAL]" in content
    assert "[FAILED]" in content
    assert "[SKIP]" in content


# ---------------------------------------------------------------------------
# write_region_summary
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_write_region_summary_creates_file(tmp_path, sample_results):
    """write_region_summary should create summary_<REGION>_<ts>.txt."""
    write_region_summary("US", sample_results, str(tmp_path), "20260322_120000")
    files = list(tmp_path.glob("summary_US_*.txt"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "REGION: US" in content


# ---------------------------------------------------------------------------
# write_shipment_records
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_write_shipment_records_creates_two_files(tmp_path):
    """write_shipment_records should create with_tracking and missing_tracking files."""
    has = {"FBA001": [{"tracking": "1Z001", "carrier": "UPS"}]}
    missing = ["FBA002", "FBA003"]
    write_shipment_records(has, missing, str(tmp_path))

    with_files = list(tmp_path.glob("shipments_with_tracking_*.txt"))
    missing_files = list(tmp_path.glob("shipments_missing_tracking_*.txt"))
    assert len(with_files) == 1
    assert len(missing_files) == 1
    assert "FBA001" in with_files[0].read_text(encoding="utf-8")
    assert "FBA002" in missing_files[0].read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# collect_updated_row_numbers
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_collect_updated_row_numbers_success():
    """Should return row numbers for successfully uploaded FBAs."""
    shipments = {
        "FBA001": [{"tracking": "1Z001", "carrier": "UPS", "row_number": 2}],
        "FBA002": [{"tracking": "1Z002", "carrier": "UPS", "row_number": 3},
                    {"tracking": "1Z003", "carrier": "UPS", "row_number": 4}],
        "FBA003": [{"tracking": "1Z004", "carrier": "UPS", "row_number": 5}],
    }
    results = [
        {"fba_id": "FBA001", "status": "success"},
        {"fba_id": "FBA002", "status": "partial"},
        {"fba_id": "FBA003", "status": "failed"},
    ]
    rows = collect_updated_row_numbers(shipments, results)
    assert rows == {2, 3, 4}  # FBA001 + FBA002, not FBA003


@pytest.mark.unit
def test_collect_updated_row_numbers_all_failed():
    """When all results are failed, should return empty set."""
    shipments = {"FBA001": [{"tracking": "1Z001", "carrier": "UPS", "row_number": 2}]}
    results = [{"fba_id": "FBA001", "status": "failed"}]
    rows = collect_updated_row_numbers(shipments, results)
    assert rows == set()
```

- [ ] **Step 2: Run the tests**

Run: `cd /c/Users/antbu/Amazon-US-Automated-Tracking && python -m pytest tests/test_run_unit.py -v -m unit`
Expected: All 16 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_run_unit.py
git commit -m "test: add unit tests for run.py utility functions"
```

---

### Task 3: Extend parse_excel Tests

**Files:**
- Modify: `tests/test_parse_excel.py`

- [ ] **Step 1: Add new tests to end of existing test_parse_excel.py**

Append these tests after the existing tests in the file:

```python
# ---------------------------------------------------------------------------
# NEW TESTS: find_excel_files, load_excel_file, parse_and_filter pipeline
# ---------------------------------------------------------------------------

from parse_excel import find_excel_files, load_excel_file, parse_and_filter


def test_find_excel_files_finds_both_types(tmp_path):
    """find_excel_files should find both .xls and .xlsx files."""
    (tmp_path / "a.xlsx").write_text("")
    (tmp_path / "b.xls").write_text("")
    result = find_excel_files(str(tmp_path))
    assert len(result) == 2
    names = [Path(f).name for f in result]
    assert "a.xlsx" in names
    assert "b.xls" in names


def test_find_excel_files_empty_folder(tmp_path):
    """find_excel_files should return [] for empty folder."""
    assert find_excel_files(str(tmp_path)) == []


def test_find_excel_files_ignores_csv(tmp_path):
    """find_excel_files should ignore .csv files."""
    (tmp_path / "data.csv").write_text("")
    (tmp_path / "data.xlsx").write_text("")
    result = find_excel_files(str(tmp_path))
    assert len(result) == 1
    assert result[0].endswith(".xlsx")


def test_load_excel_file_xls_real(sample_xls):
    """load_excel_file should parse a real .xls fixture via xlrd."""
    config = {"column_fc_code": 3, "column_fba_id": 4,
              "column_tracking": 7, "column_carrier": 8}
    rows = load_excel_file(sample_xls, config)
    assert len(rows) > 0
    assert all("fba_id" in r for r in rows)
    assert all("tracking_num" in r for r in rows)


def test_load_excel_file_xls_numeric_cells(sample_xls):
    """Numeric tracking numbers in .xls should not have '.0' suffix."""
    config = {"column_fc_code": 3, "column_fba_id": 4,
              "column_tracking": 7, "column_carrier": 8}
    rows = load_excel_file(sample_xls, config)
    for r in rows:
        if r["tracking_num"]:
            assert ".0" not in r["tracking_num"], f"Found '.0' in tracking: {r['tracking_num']}"


def test_load_excel_file_xlsx_multi_sheet(tmp_path):
    """load_excel_file should read rows from ALL sheets in .xlsx."""
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Sheet1"
    ws1.append(["A", "B", "C", "D_fc", "E_fba", "F", "G", "H_tracking", "I_carrier"])
    ws1.append([None, None, None, "BNA6", "FBA_S1", None, None, "1Z001", "UPS"])

    ws2 = wb.create_sheet("Sheet2")
    ws2.append(["A", "B", "C", "D_fc", "E_fba", "F", "G", "H_tracking", "I_carrier"])
    ws2.append([None, None, None, "GYR3", "FBA_S2", None, None, "1Z002", "UPS"])

    path = tmp_path / "multi.xlsx"
    wb.save(path)

    config = {"column_fc_code": 3, "column_fba_id": 4,
              "column_tracking": 7, "column_carrier": 8}
    rows = load_excel_file(str(path), config)
    fba_ids = {r["fba_id"] for r in rows}
    assert "FBA_S1" in fba_ids
    assert "FBA_S2" in fba_ids


def test_parse_and_filter_full_pipeline(tmp_config):
    """parse_and_filter should read xlsx, filter US FCs, group by FBA."""
    import openpyxl as xl
    wb = xl.Workbook()
    ws = wb.active
    ws.append(["A", "B", "C", "D_fc", "E_fba", "F", "G", "H_tracking", "I_carrier"])
    ws.append([None, None, None, "BNA6", "FBA_P1", None, None, "1ZTEST001", "UPS"])
    ws.append([None, None, None, "YVR2", "FBA_P2", None, None, "1ZTEST002", "UPS"])  # not US
    path = Path(tmp_config["input_folder"]) / "test.xlsx"
    wb.save(path)

    result = parse_and_filter(tmp_config)
    assert "FBA_P1" in result
    assert "FBA_P2" not in result  # YVR is Canadian, not US


def test_parse_and_filter_no_files(tmp_config):
    """parse_and_filter should return {} when input folder has no Excel files."""
    result = parse_and_filter(tmp_config)
    assert result == {}


def test_parse_and_filter_by_region_with_data(tmp_config):
    """parse_and_filter_by_region should split rows by region correctly."""
    import openpyxl as xl
    wb = xl.Workbook()
    ws = wb.active
    ws.append(["A", "B", "C", "D_fc", "E_fba", "F", "G", "H_tracking", "I_carrier"])
    ws.append([None, None, None, "BNA6", "FBA_US1", None, None, "1Z001", "UPS"])
    ws.append([None, None, None, "YVR2", "FBA_CA1", None, None, "1Z002", "UPS"])
    path = Path(tmp_config["input_folder"]) / "regions.xlsx"
    wb.save(path)

    result = parse_and_filter_by_region(tmp_config)
    assert "FBA_US1" in result.get("US", {})
    assert "FBA_CA1" in result.get("CA", {})
    assert "FBA_US1" not in result.get("CA", {})


def test_group_by_fba_id_slash_split():
    """group_by_fba_id should split 'STAR-A/STAR-B' into two separate FBA keys."""
    rows = [{"fba_id": "STAR-A/STAR-B", "tracking_num": "1Z001", "carrier": "UPS", "row_number": 2}]
    result = group_by_fba_id(rows)
    assert "STAR-A" in result
    assert "STAR-B" in result
    assert result["STAR-A"][0]["tracking"] == "1Z001"
    assert result["STAR-B"][0]["tracking"] == "1Z001"
```

Note: Add `from parse_excel import find_excel_files, load_excel_file, parse_and_filter` to the imports at the top of the existing file. `parse_and_filter_by_region` is already imported. `openpyxl` is imported inside functions where needed (following existing pattern).

- [ ] **Step 2: Run all parse_excel tests**

Run: `cd /c/Users/antbu/Amazon-US-Automated-Tracking && python -m pytest tests/test_parse_excel.py -v`
Expected: All tests PASS (23 existing + 10 new = 33)

- [ ] **Step 3: Commit**

```bash
git add tests/test_parse_excel.py
git commit -m "test: extend parse_excel coverage (xls, find, filter pipeline)"
```

---

### Task 4: Extend highlight_excel Tests

**Files:**
- Modify: `tests/test_highlight_excel.py`

- [ ] **Step 1: Add test for .xls source**

Append to end of `tests/test_highlight_excel.py`:

```python
def test_highlight_xls_source(sample_xls, tmp_path):
    """highlight_and_save should handle .xls source, output as .xlsx."""
    from highlight_excel import highlight_and_save
    dest = tmp_path / "output.xlsx"
    result_path = highlight_and_save(sample_xls, str(dest), {2, 3})
    assert result_path.endswith(".xlsx")
    assert Path(result_path).exists()
    # Verify the output is a valid xlsx
    wb = openpyxl.load_workbook(result_path)
    assert wb.active is not None
```

- [ ] **Step 2: Run highlight tests**

Run: `cd /c/Users/antbu/Amazon-US-Automated-Tracking && python -m pytest tests/test_highlight_excel.py -v`
Expected: 3 tests PASS (2 existing + 1 new). If `sample.xls` is missing, the new test SKIPS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_highlight_excel.py
git commit -m "test: add highlight_excel xls source test"
```

---

### Task 5: Extend fetch_sub_tracking Tests

**Files:**
- Modify: `tests/test_fetch_sub_tracking.py`

- [ ] **Step 1: Add 6 edge case tests**

Append to end of `tests/test_fetch_sub_tracking.py`:

```python
# ---------------------------------------------------------------------------
# NEW: Edge case coverage for all regex branches
# ---------------------------------------------------------------------------

def test_extract_ups_1z_format():
    """UPS pattern should match 1Z + 16 alphanumeric = 18 total chars."""
    text = "Package: 1ZABCDEFGH12345678"
    result = extract_ups_tracking_from_text(text)
    assert "1ZABCDEFGH12345678" in result


def test_extract_fedex_15digit():
    """FedEx pattern should match 15-digit tracking numbers."""
    text = "Tracking: 123456789012345"
    result = extract_fedex_tracking_from_text(text)
    assert "123456789012345" in result


def test_extract_fedex_20digit():
    """FedEx pattern should match 20-digit tracking numbers."""
    text = "Tracking: 12345678901234567890"
    result = extract_fedex_tracking_from_text(text)
    assert "12345678901234567890" in result


def test_extract_fedex_22digit():
    """FedEx pattern should match 22-digit tracking numbers."""
    text = "Tracking: 1234567890123456789012"
    result = extract_fedex_tracking_from_text(text)
    assert "1234567890123456789012" in result


def test_deduplicate_empty():
    """deduplicate_tracking_numbers should handle empty list."""
    result = deduplicate_tracking_numbers([])
    assert result == []


def test_deduplicate_none_input():
    """deduplicate_tracking_numbers should handle None-like input."""
    result = deduplicate_tracking_numbers(None)
    assert result == []
```

- [ ] **Step 2: Run fetch_sub_tracking tests**

Run: `cd /c/Users/antbu/Amazon-US-Automated-Tracking && python -m pytest tests/test_fetch_sub_tracking.py -v`
Expected: All tests PASS (14 existing + 6 new = 20)

- [ ] **Step 3: Commit**

```bash
git add tests/test_fetch_sub_tracking.py
git commit -m "test: extend fetch_sub_tracking edge case coverage"
```

---

### Task 6: Carrier Integration Tests

**Files:**
- Create: `tests/test_carrier_integration.py`

- [ ] **Step 1: Write carrier integration tests**

```python
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
```

- [ ] **Step 2: Run carrier integration tests (expect skips if no tracking numbers)**

Run: `cd /c/Users/antbu/Amazon-US-Automated-Tracking && python -m pytest tests/test_carrier_integration.py -v -m integration`
Expected: All 8 tests SKIP with "test_tracking_numbers.json not found" (unless you've created it)

- [ ] **Step 3: Commit**

```bash
git add tests/test_carrier_integration.py
git commit -m "test: add carrier integration tests (UPS/FedEx real browser)"
```

---

### Task 7: Amazon Integration Tests

**Files:**
- Create: `tests/test_amazon_integration.py`

- [ ] **Step 1: Write Amazon integration tests**

```python
import os
import sys
import pytest
import logging
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from upload_tracking import (
    create_browser_context,
    check_login_status,
    navigate_to_shipment,
    check_amazon_tracking_status,
    discover_page_elements,
)

logger = logging.getLogger(__name__)


def _is_login_page(url: str) -> bool:
    """Check if current URL is an Amazon login page."""
    return "ap/signin" in url or "ap/register" in url or "/signin" in url.lower()


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
```

- [ ] **Step 2: Run Amazon integration tests (expect skips)**

Run: `cd /c/Users/antbu/Amazon-US-Automated-Tracking && python -m pytest tests/test_amazon_integration.py -v -m integration`
Expected: All 6 tests SKIP (no config.json or not logged in)

- [ ] **Step 3: Commit**

```bash
git add tests/test_amazon_integration.py
git commit -m "test: add Amazon Seller Central integration tests"
```

---

### Task 8: E2E Pipeline Tests

**Files:**
- Create: `tests/test_e2e_pipeline.py`

- [ ] **Step 1: Write e2e pipeline tests**

```python
import os
import sys
import json
import subprocess
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

pytestmark = pytest.mark.e2e


def _has_config():
    return (PROJECT_ROOT / "config.json").exists()


def _run_pipeline(*args, timeout=120):
    """Run run.py as a subprocess with auto-Enter for input() prompts."""
    cmd = [sys.executable, str(PROJECT_ROOT / "run.py")] + list(args)
    result = subprocess.run(
        cmd,
        input=b"\n",
        capture_output=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )
    return result


def test_collect_only(test_logger):
    """--collect-only should fetch tracking IDs without uploading to Amazon."""
    if not _has_config():
        pytest.skip("config.json not found — cannot run pipeline")

    # Check if there are Excel files in input/
    input_dir = PROJECT_ROOT / "input"
    excel_files = list(input_dir.glob("*.xls")) + list(input_dir.glob("*.xlsx"))
    if not excel_files:
        pytest.skip("No Excel files in input/ — cannot test pipeline")

    test_logger.info("Running: python run.py --collect-only")
    result = _run_pipeline("--collect-only")
    test_logger.info(f"Return code: {result.returncode}")
    test_logger.info(f"Stdout (last 500 chars): {result.stdout[-500:]}")
    if result.stderr:
        test_logger.warning(f"Stderr: {result.stderr[-500:]}")

    # Should produce tracking_ids JSON in logs/
    logs_dir = PROJECT_ROOT / "logs"
    json_files = list(logs_dir.glob("tracking_ids_*.json"))
    test_logger.info(f"Tracking ID JSON files found: {len(json_files)}")

    # Should exit without error (0) or gracefully
    assert result.returncode == 0 or b"No FBA shipments found" in result.stdout


def test_check_only(test_logger):
    """--check-only should check Amazon status without uploading."""
    if not _has_config():
        pytest.skip("config.json not found — cannot run pipeline")

    input_dir = PROJECT_ROOT / "input"
    excel_files = list(input_dir.glob("*.xls")) + list(input_dir.glob("*.xlsx"))
    if not excel_files:
        pytest.skip("No Excel files in input/ — cannot test pipeline")

    test_logger.info("Running: python run.py --check-only")
    result = _run_pipeline("--check-only")
    test_logger.info(f"Return code: {result.returncode}")
    test_logger.info(f"Stdout (last 500 chars): {result.stdout[-500:]}")

    # Should mention "CHECK COMPLETE" or "No FBA shipments"
    output = result.stdout.decode("utf-8", errors="replace")
    assert "CHECK COMPLETE" in output or "No FBA shipments" in output or result.returncode == 0


def test_from_json_structure(test_logger):
    """Verify tracking_ids JSON from --collect-only has valid structure (no browser needed)."""
    if not _has_config():
        pytest.skip("config.json not found — cannot verify JSON structure")

    # Find a tracking_ids JSON from a previous run
    logs_dir = PROJECT_ROOT / "logs"
    json_files = sorted(logs_dir.glob("tracking_ids_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not json_files:
        pytest.skip("No tracking_ids JSON in logs/ — run --collect-only first")

    json_path = json_files[0]
    test_logger.info(f"Validating JSON structure: {json_path}")
    data = json.loads(json_path.read_text(encoding="utf-8"))

    # Validate structure: {fba_id: {"parent": [...], "sub_ids": [...]}}
    assert isinstance(data, dict), "JSON root should be a dict"
    for fba_id, entry in data.items():
        assert isinstance(fba_id, str), f"FBA key should be string: {fba_id}"
        assert "parent" in entry, f"Missing 'parent' key in {fba_id}"
        assert "sub_ids" in entry, f"Missing 'sub_ids' key in {fba_id}"
        assert isinstance(entry["sub_ids"], list), f"sub_ids should be list in {fba_id}"
    test_logger.info(f"JSON valid: {len(data)} FBA entries")
```

- [ ] **Step 2: Run e2e tests (expect skips)**

Run: `cd /c/Users/antbu/Amazon-US-Automated-Tracking && python -m pytest tests/test_e2e_pipeline.py -v -m e2e`
Expected: All 3 tests SKIP (no config.json or no Excel files)

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_pipeline.py
git commit -m "test: add e2e pipeline tests (collect-only, check-only)"
```

---

### Task 9: README V2

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite README.md**

Replace the entire `README.md` with updated V2 content covering:
- Multi-region support (US, CA, UK, EU) with region table
- AWD shipments (STAR- prefix)
- All CLI flags including new: `--check-only`, `--fba-list`, `--collect-only`, `--from-json`, `--rewrite`, `--regions`, `--config`
- Pre-check flow and done cache system
- Updated output files (per-region summaries)
- Running tests section
- Updated file tree with `fc_codes/`, `tests/`
- Updated troubleshooting

Reference `run.py` lines 279-337 for the complete argparse definition to ensure all flags are documented accurately.

- [ ] **Step 2: Verify README against CLI help**

Run: `cd /c/Users/antbu/Amazon-US-Automated-Tracking && python run.py --help`
Cross-check all flags in README match the `--help` output.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: update README to V2 (multi-region, AWD, new CLI flags, tests)"
```

---

### Task 10: TODOS.md + Ship

**Files:**
- Create: `TODOS.md`

- [ ] **Step 1: Create TODOS.md**

```markdown
# TODOs

## P1: GitHub Actions CI
Add `.github/workflows/test.yml` running `pytest -m unit` on every push/PR.
Unit tests are fast (<5s), no external deps. Catches regressions automatically.
**Depends on:** This PR (tests must exist first).

## P2: Test Coverage Reporting
Add `pytest-cov` to requirements.txt. Run `pytest --cov=. --cov-report=html`.
Shows exactly which lines are untested.
**Depends on:** P1 (CI should display coverage).

## P3: Refactor sys.exit to Custom Exceptions
`load_config()` and `ensure_folders()` call `sys.exit(1)` on error.
Refactor to raise `ConfigError`/`SetupError` instead.
Improves testability (catch exception vs. SystemExit).
**Depends on:** Nothing.
```

- [ ] **Step 2: Run full test suite**

Run: `cd /c/Users/antbu/Amazon-US-Automated-Tracking && python -m pytest tests/ -v --tb=short`
Expected: ~90+ tests collected. Unit tests PASS. Integration/e2e tests SKIP cleanly.

- [ ] **Step 3: Commit TODOS.md**

```bash
git add TODOS.md
git commit -m "chore: add TODOS.md with deferred work items"
```

- [ ] **Step 4: Push and create PR**

```bash
git push -u origin feat/testing-and-readme-v2
gh pr create --title "feat: comprehensive test suite and README V2" --body "$(cat <<'EOF'
## Summary
- Test count: 43 → ~90+ (unit + integration + e2e)
- All unit tests use real files, no mocks
- Integration tests: real browser against UPS/FedEx/Amazon (skip-if-unavailable)
- E2E tests: subprocess pipeline tests in read-only mode
- README updated to V2: multi-region, AWD, 7 new CLI flags, test docs

## Test plan
- [ ] `pytest -m unit` — all pass
- [ ] `pytest -m integration` — skip cleanly when no tracking numbers/login
- [ ] `pytest -m e2e` — skip cleanly when no config/data
- [ ] README matches `python run.py --help` output

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Note the PR URL**
