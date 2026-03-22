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
    ws.append(["A", "B", "C", "D_fc", "E_fba", "F", "G", "H_tracking", "I_carrier"])
    ws.append([None, None, None, "BNA6", "FBA_TEST_001", None, None, "1ZTEST00000000001", "UPS"])
    ws.append([None, None, None, "GYR3", "FBA_TEST_002", None, None, "000000000012", "FedEx"])
    ws.append([None, None, None, "IND1", "FBA_TEST_001", None, None, "1ZTEST00000000002", "UPS"])
    ws.append([None, None, None, "YVR2", "FBA_TEST_CA1", None, None, "1ZTEST00000000003", "UPS"])
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
    markers = {m.name for m in request.node.iter_markers()}
    if not (markers & {"integration", "e2e"}):
        return
    if hasattr(request.node, "rep_call") and request.node.rep_call.failed:
        page = request.node.funcargs.get("browser_page")
        if page:
            try:
                ss_dir = TEST_LOGS_DIR / "screenshots"
                ss_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                page.screenshot(path=str(ss_dir / f"{ts}_{request.node.name}.png"))
            except Exception:
                pass


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store test result on the item for screenshot_on_failure fixture."""
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
