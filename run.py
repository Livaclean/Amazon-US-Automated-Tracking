# run.py
import json
import logging
import sys
import argparse
from datetime import datetime
from pathlib import Path


def setup_logging(logs_folder: str) -> None:
    """Creates logger writing to console (INFO) and timestamped file in logs/ (DEBUG)."""
    Path(logs_folder).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = Path(logs_folder) / f"tracking_upload_{ts}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[file_handler, console_handler],
    )
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger(__name__).info(f"Log file: {log_file}")


def load_config(config_path: str = "config.json") -> dict:
    """Loads config.json with sensible defaults for optional keys."""
    if not Path(config_path).exists():
        print(f"ERROR: config.json not found. Expected at: {config_path}")
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    defaults = {
        "amazon_base_url": "https://sellercentral.amazon.com",
        "headless": False,
        "delay_between_shipments_seconds": 2,
        "delay_between_tracking_numbers_seconds": 1,
        "column_fc_code": 3,
        "column_fba_id": 4,
        "column_tracking": 7,
        "column_carrier": 8,
        "us_fc_codes_file": "fc_codes/us_fc_codes.txt",
    }
    for k, v in defaults.items():
        config.setdefault(k, v)
    return config


def ensure_folders(config: dict) -> None:
    """Creates input/output/logs/screenshots folders if they don't exist."""
    for key in ["input_folder", "output_folder", "logs_folder"]:
        if key not in config:
            print(f"ERROR: '{key}' is missing from config.json")
            sys.exit(1)
        Path(config[key]).mkdir(parents=True, exist_ok=True)
    screenshots_folder = Path(config["logs_folder"]) / "screenshots"
    screenshots_folder.mkdir(parents=True, exist_ok=True)
    for old_png in screenshots_folder.glob("*.png"):
        old_png.unlink(missing_ok=True)


def cleanup_logs(logs_folder: str) -> None:
    """
    Cleans up old log files at the start of each run.
    - Keeps completed_fba_*.txt (persistent done caches) always.
    - Keeps only the 3 most recent files of each timestamped type.
    - Deletes all one-off debug/temp files (fedex_page_*, ups_page_*, debug_*, etc.).
    """
    logs = Path(logs_folder)

    # One-off debug/temp patterns to delete entirely
    debug_patterns = [
        "fedex_page_*.txt", "fedex_*.txt", "ups_page_*.txt", "ups_*.txt",
        "debug_*.*", "page_discovery_*.txt", "precheck_result.json",
        "retry_*.txt", "test_*.png", "test_*.txt",
        "group*_*.txt", "*_ready.txt", "*_notfound.txt",
        "new_us_fba_list.txt", "non_us_fba_list*.txt",
        "ups_shipment_response.json", "fedex_api_response.json",
    ]
    deleted = 0
    for pattern in debug_patterns:
        for f in logs.glob(pattern):
            f.unlink(missing_ok=True)
            deleted += 1

    # Timestamped file types — keep only the 3 most recent of each group
    # Group by prefix (everything before the timestamp)
    import re
    timestamped_patterns = [
        "tracking_upload_*.log",
        "shipments_with_tracking_*.txt",
        "shipments_missing_tracking_*.txt",
        "amazon_already_complete_*.txt",
        "amazon_needs_upload_*.txt",
        "summary_*.txt",
        "tracking_ids_*.json",
    ]
    for pattern in timestamped_patterns:
        files = sorted(logs.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
        # Group by non-timestamp prefix to keep last 3 per type/region
        groups: dict = {}
        for f in files:
            # Strip trailing timestamp: remove last _YYYYMMDD_HHMMSS or _YYYY-MM-DD_HH-MM-SS
            key = re.sub(r'[_-]\d{8}[_-]\d{6}$', '', f.stem)
            groups.setdefault(key, []).append(f)
        for group_files in groups.values():
            for old_file in group_files[3:]:  # keep 3 most recent
                old_file.unlink(missing_ok=True)
                deleted += 1

    if deleted:
        print(f"  Cleaned up {deleted} old log file(s).")


def write_summary(results: list, logs_folder: str) -> None:
    """Writes a human-readable summary report to logs/."""
    now = datetime.now()
    ts_display = now.strftime("%Y-%m-%d %H:%M:%S")
    ts_file = now.strftime("%Y%m%d_%H%M%S")
    icons = {
        "success": "[OK]      ",
        "partial": "[PARTIAL] ",
        "failed": "[FAILED]  ",
        "not_found": "[NOTFOUND]",
        "skipped": "[SKIP]    ",
    }
    lines = [
        "=" * 60,
        f"TRACKING UPLOAD SUMMARY - {ts_display}",
        "=" * 60,
        f"Total FBA shipments: {len(results)}",
        f"  Successful:   {sum(1 for r in results if r['status'] == 'success')}",
        f"  Partial:      {sum(1 for r in results if r['status'] == 'partial')}",
        f"  Failed:       {sum(1 for r in results if r['status'] in ('failed', 'not_found'))}",
        f"  Skipped:      {sum(1 for r in results if r['status'] == 'skipped')}",
        "",
        "DETAILS:",
    ]
    for r in results:
        icon = icons.get(r["status"], "[?]       ")
        lines.append(
            f"  {icon} {r['fba_id']}  - "
            f"{r.get('succeeded', 0)} added, "
            f"{r.get('already_existed', 0)} existed, "
            f"{r.get('failed', 0)} failed "
            f"(of {r.get('total', 0)})"
        )
    lines.append("=" * 60)

    report = Path(logs_folder) / f"summary_{ts_file}.txt"
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines))


def write_region_summary(region_name: str, results: list, logs_folder: str, timestamp: str) -> None:
    """Writes a per-region summary file to logs/summary_<REGION>_<timestamp>.txt."""
    icons = {
        "success": "[OK]      ",
        "partial": "[PARTIAL] ",
        "failed": "[FAILED]  ",
        "not_found": "[NOTFOUND]",
        "skipped": "[SKIP]    ",
    }
    lines = [
        "=" * 60,
        f"REGION: {region_name} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
        f"Total FBA shipments: {len(results)}",
        f"  Successful: {sum(1 for r in results if r['status'] == 'success')}",
        f"  Partial:    {sum(1 for r in results if r['status'] == 'partial')}",
        f"  Failed:     {sum(1 for r in results if r['status'] in ('failed', 'not_found'))}",
        f"  Skipped:    {sum(1 for r in results if r['status'] == 'skipped')}",
        "",
        "DETAILS:",
    ]
    for r in results:
        icon = icons.get(r["status"], "[?]       ")
        lines.append(
            f"  {icon} {r['fba_id']}  - "
            f"{r.get('succeeded', 0)} added, "
            f"{r.get('already_existed', 0)} existed, "
            f"{r.get('failed', 0)} failed "
            f"(of {r.get('total', 0)})"
        )
    lines.append("=" * 60)

    report = Path(logs_folder) / f"summary_{region_name}_{timestamp}.txt"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[{region_name}] Summary saved: {report.name}")


def wait_for_login(page, region_name: str, amazon_url: str, timeout_seconds: int = 300) -> bool:
    """
    Navigates to amazon_url and checks if we're logged in by inspecting the final URL.
    If redirected to ap/signin, prints a prompt and polls the current URL every 3 seconds
    WITHOUT navigating — letting the user log in uninterrupted.
    Returns True once no longer on a signin page, False if timed out.
    """
    import time
    logger = logging.getLogger(__name__)

    def _is_login_url(url: str) -> bool:
        return "ap/signin" in url or "ap/register" in url or "/signin" in url.lower()

    try:
        page.goto(amazon_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        logger.warning(f"[{region_name}] Failed to navigate to {amazon_url}: {e}")
        return False

    if not _is_login_url(page.url):
        logger.info(f"[{region_name}] Already logged in at {amazon_url}")
        return True

    print(f"\n[{region_name}] NOT logged in.")
    print(f"[{region_name}] Please log in in the browser. Waiting up to {timeout_seconds // 60} minutes...")
    print(f"[{region_name}] (Do NOT close the browser — log in and we'll detect it automatically)")

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(3)
        try:
            current_url = page.url
            if not _is_login_url(current_url):
                print(f"[{region_name}] Login detected! Proceeding...")
                return True
        except Exception:
            pass

    logger.warning(f"[{region_name}] Login timed out after {timeout_seconds}s — skipping region")
    print(f"[{region_name}] Login timed out — skipping this region.")
    return False


def write_shipment_records(has_tracking: dict, missing_tracking: list, logs_folder: str) -> None:
    """Writes two plain-text files: one for FBAs with tracking, one for those missing it."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logs = Path(logs_folder)

    with_file = logs / f"shipments_with_tracking_{ts}.txt"
    with_file.write_text("\n".join(sorted(has_tracking.keys())), encoding="utf-8")

    missing_file = logs / f"shipments_missing_tracking_{ts}.txt"
    missing_file.write_text("\n".join(sorted(missing_tracking)), encoding="utf-8")

    logging.getLogger(__name__).info(
        f"Shipment records written:\n"
        f"  With tracking ({len(has_tracking)}): {with_file}\n"
        f"  Missing tracking ({len(missing_tracking)}): {missing_file}"
    )
    print(f"\nShipment records saved:")
    print(f"  With tracking: {len(has_tracking)} FBAs -> {with_file.name}")
    print(f"  Missing tracking: {len(missing_tracking)} FBAs -> {missing_file.name}")


def collect_updated_row_numbers(shipments_all: dict, results: list) -> set:
    """
    Returns set of 1-indexed Excel row numbers for FBA IDs that were successfully uploaded.
    shipments_all: full grouped dict from parse_and_filter (includes row_number per entry)
    results: list of upload result dicts with 'fba_id' and 'status'
    """
    successful_fba_ids = {
        r["fba_id"] for r in results if r["status"] in ("success", "partial")
    }
    rows = set()
    for fba_id in successful_fba_ids:
        for entry in shipments_all.get(fba_id, []):
            rn = entry.get("row_number")
            if rn:
                rows.add(rn)
    return rows


def main():
    parser = argparse.ArgumentParser(description="Amazon FBA Tracking Number Uploader")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Dump Amazon page elements to logs/ (run once on first use to find selectors)",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config.json (default: config.json in current directory)",
    )
    parser.add_argument(
        "--fba-id",
        default=None,
        help="Specific FBA ID to use for --discover (default: first from Excel)",
    )
    parser.add_argument(
        "--skip-carrier",
        action="store_true",
        help="Skip UPS/FedEx scraping and upload main tracking numbers directly to Amazon",
    )
    parser.add_argument(
        "--only-fba",
        default=None,
        help="Run the full pipeline (carrier scrape + upload) for one specific FBA ID only",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Check Amazon for each shipment's tracking status without uploading anything",
    )
    parser.add_argument(
        "--fba-list",
        default=None,
        help="Path to a text file with FBA IDs (one per line) — limits processing to only those IDs",
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Fetch main + sub tracking numbers and save to JSON, but do NOT upload to Amazon",
    )
    parser.add_argument(
        "--from-json",
        default=None,
        help="Path to a tracking_ids JSON file — skip Excel parsing and carrier scraping, upload directly",
    )
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help="Overwrite tracking inputs that already have a value (force rewrite)",
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        default=None,
        metavar="REGION",
        help="Limit which regions to run (e.g. --regions US CA). Default: all regions in config.",
    )
    args = parser.parse_args()

    # Pre-initialize so these are always in scope even if an early exception occurs
    results = []
    shipments_all = {}

    config = load_config(args.config)
    ensure_folders(config)
    cleanup_logs(config["logs_folder"])
    setup_logging(config["logs_folder"])

    # Import project modules after logging is configured
    from parse_excel import parse_and_filter, parse_and_filter_by_region, categorize_shipments
    from fetch_sub_tracking import get_all_sub_tracking, check_fedex_login
    from upload_tracking import (
        create_browser_context,
        check_login_status,
        discover_page_elements,
        upload_all_shipments,
        check_all_shipments_on_amazon,
        get_slot_count,
    )
    from highlight_excel import highlight_and_save

    logger = logging.getLogger(__name__)

    # Determine which regions to run
    configured_regions = config.get("regions", [])
    if not configured_regions:
        # Backward compat: no regions in config → US only using amazon_base_url
        configured_regions = [{
            "name": "US",
            "amazon_url": config.get("amazon_base_url", "https://sellercentral.amazon.com"),
            "fc_codes_file": config.get("us_fc_codes_file", "fc_codes/us_fc_codes.txt"),
        }]

    if args.regions:
        allowed = set(args.regions)
        configured_regions = [r for r in configured_regions if r["name"] in allowed]
        if not configured_regions:
            print(f"\nERROR: None of the specified --regions ({args.regions}) found in config.")
            return

    # Step 1: Parse Excel — load all regions at once
    logger.info("Reading Excel file from input folder...")
    all_regions_data = parse_and_filter_by_region(config)
    # Also keep a flat dict for backward-compat (highlight_excel, --from-json, etc.)
    shipments_all = {}
    for region_data in all_regions_data.values():
        shipments_all.update(region_data)

    if args.only_fba:
        for name in all_regions_data:
            if args.only_fba in all_regions_data[name]:
                all_regions_data[name] = {args.only_fba: all_regions_data[name][args.only_fba]}
            else:
                all_regions_data[name] = {}
        found_in = [n for n in all_regions_data if all_regions_data[n]]
        if not found_in:
            print(f"\nERROR: FBA ID '{args.only_fba}' not found in any region.")
            return
        shipments_all = {}
        for region_data in all_regions_data.values():
            shipments_all.update(region_data)
        print(f"\nRunning for single shipment: {args.only_fba} (found in: {', '.join(found_in)})")

    if args.fba_list:
        fba_list_path = Path(args.fba_list)
        if not fba_list_path.exists():
            print(f"\nERROR: FBA list file not found: {args.fba_list}")
            return
        fba_ids = {line.strip() for line in fba_list_path.read_text(encoding="utf-8").splitlines() if line.strip()}
        for name in all_regions_data:
            all_regions_data[name] = {fba: v for fba, v in all_regions_data[name].items() if fba in fba_ids}
        shipments_all = {}
        for region_data in all_regions_data.values():
            shipments_all.update(region_data)
        print(f"\nFiltered to {len(shipments_all)} FBA(s) from list: {fba_list_path.name}")

    if not shipments_all:
        print(f"\nNo FBA shipments found in any configured region.")
        print(f"  - Drop your Excel file in:  {config['input_folder']}")
        print(f"  - Check column D has FC codes matching your regions")
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
        return

    # Categorize for carrier scrape (has_tracking only)
    shipments_raw, missing_tracking = categorize_shipments(shipments_all)
    write_shipment_records(shipments_raw, missing_tracking, config["logs_folder"])

    if missing_tracking:
        print(f"\n  {len(missing_tracking)} FBA(s) have no usable tracking — recorded to logs.")

    total_main = sum(len(v) for v in shipments_raw.values())
    print(f"\nFound {len(shipments_raw)} FBA shipments with {total_main} trackable entries across {len(configured_regions)} region(s).")

    # --from-json: skip Excel + carrier scraping, load tracking directly from JSON
    if args.from_json:
        json_path = Path(args.from_json)
        if not json_path.exists():
            print(f"\nERROR: JSON file not found: {args.from_json}")
            return
        raw_data = json.loads(json_path.read_text(encoding="utf-8"))

        # Build full tracking pool per main tracking number: [main] + sub_ids
        # Group FBAs that share the same main tracking
        tracking_groups = {}  # main_tracking -> {"fba_ids": [...], "pool": [...]}
        for fba_id, entry in raw_data.items():
            parents = entry.get("parent", [])
            sub_ids = entry.get("sub_ids", [])
            for p in parents:
                main = p.get("tracking")
                if not main:
                    continue
                if main not in tracking_groups:
                    pool = [main] + [s for s in sub_ids if s != main]
                    tracking_groups[main] = {"fba_ids": [], "pool": pool}
                tracking_groups[main]["fba_ids"].append(fba_id)

        print(f"\nLoaded {len(raw_data)} FBA shipment(s) from {json_path.name}")
        for main, grp in tracking_groups.items():
            if len(grp["fba_ids"]) > 1:
                print(f"  Shared tracking {main}: {grp['fba_ids']} — will check Amazon slot counts to split pool of {len(grp['pool'])}")

        logger.info("Launching Chrome with your saved profile...")
        try:
            pw, context = create_browser_context(config)
        except RuntimeError as e:
            print(f"\nERROR: {e}")
            return
        page = context.new_page()
        results = []
        try:
            check_login_status(page, config["amazon_base_url"])
            base_url = config.get("amazon_base_url", "https://sellercentral.amazon.com")

            # For groups with multiple FBAs sharing the same main tracking,
            # pre-check Amazon to get slot count per FBA, then distribute pool sequentially
            shipments_with_subs = {}
            for main, grp in tracking_groups.items():
                fba_ids = grp["fba_ids"]
                pool = grp["pool"]
                if len(fba_ids) == 1:
                    shipments_with_subs[fba_ids[0]] = pool
                else:
                    print(f"\n  Checking slot counts for shared tracking {main}...")
                    slot_counts = {}
                    for fba_id in fba_ids:
                        count = get_slot_count(page, fba_id, base_url)
                        slot_counts[fba_id] = count
                        print(f"    {fba_id}: {count} slot(s)")

                    pool_idx = 0
                    for fba_id in fba_ids:
                        n = slot_counts.get(fba_id, 0)
                        assigned = pool[pool_idx: pool_idx + n]
                        pool_idx += n
                        shipments_with_subs[fba_id] = assigned
                        print(f"    -> {fba_id} assigned: {assigned}")

                    if pool_idx < len(pool):
                        logger.warning(f"  Pool has {len(pool)} IDs but only {pool_idx} slots assigned — {pool[pool_idx:]} left over")

            print("\nUploading to Amazon Seller Central...")
            results = upload_all_shipments(shipments_with_subs, config, page, force=args.rewrite)
        finally:
            context.close()
            pw.stop()
        write_summary(results, config["logs_folder"])
        try:
            input("\nPress Enter to close this window...")
        except EOFError:
            pass
        return

    # Step 2: Launch browser (one session for carrier sites + Amazon)
    logger.info("Launching Chrome with your saved profile...")
    try:
        pw, context = create_browser_context(config)
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
        return

    page = context.new_page()
    results = []

    try:
        # Discovery mode - dumps page elements for first-run selector identification
        if args.discover:
            first_fba = args.fba_id if args.fba_id else next(iter(shipments_raw))
            logger.info(f"Discovery mode: opening shipment page for {first_fba}")
            print("Discovery mode: you may need to log in to Amazon Seller Central manually.")
            check_login_status(page, config["amazon_base_url"])
            discover_page_elements(
                page, first_fba, config["amazon_base_url"], config["logs_folder"]
            )
            print("\nDiscovery complete. Review the logs/ folder.")
            return

        # Check-only mode: visit each FBA on Amazon, record status, do NOT upload
        if args.check_only:
            ts_chk = datetime.now().strftime("%Y%m%d_%H%M%S")
            logs = Path(config["logs_folder"])
            all_needs_upload = {}
            all_already_complete = []

            for region in configured_regions:
                region_name = region["name"]
                amazon_url = region["amazon_url"]
                region_fba_ids = set(all_regions_data.get(region_name, {}).keys())
                region_shipments = {fba: shipments_raw[fba] for fba in region_fba_ids if fba in shipments_raw}
                if not region_shipments:
                    print(f"\n[{region_name}] No shipments to check — skipping.")
                    continue

                print(f"\n[CHECK ONLY] [{region_name}] Logging into {amazon_url}...")
                logged_in = wait_for_login(page, region_name, amazon_url, timeout_seconds=300)
                if not logged_in:
                    print(f"[{region_name}] Login timed out — skipping.")
                    continue

                region_config = dict(config)
                region_config["amazon_base_url"] = amazon_url
                print(f"\n[{region_name}] Checking {len(region_shipments)} shipments (read-only)...\n")
                needs_upload, already_complete = check_all_shipments_on_amazon(region_shipments, region_config, page)
                all_needs_upload.update(needs_upload)
                all_already_complete.extend(already_complete)

                complete_file = logs / f"amazon_already_complete_{region_name}_{ts_chk}.txt"
                pending_file = logs / f"amazon_needs_upload_{region_name}_{ts_chk}.txt"
                complete_file.write_text("\n".join(sorted(already_complete)), encoding="utf-8")
                pending_file.write_text("\n".join(sorted(needs_upload.keys())), encoding="utf-8")
                print(f"\n[{region_name}] Already complete : {len(already_complete)} -> {complete_file.name}")
                print(f"[{region_name}] Needs upload     : {len(needs_upload)} -> {pending_file.name}")

            print(f"\n{'='*60}")
            print(f"CHECK COMPLETE (nothing was uploaded)")
            print(f"  Already have tracking : {len(all_already_complete)}")
            print(f"  Missing tracking      : {len(all_needs_upload)}")
            print(f"{'='*60}")
            return

        # Step 3: Region loop — for each region: login → pre-check Amazon → carrier scrape → upload
        ts_run = datetime.now().strftime("%Y%m%d_%H%M%S")
        all_results = []

        for region in configured_regions:
            region_name = region["name"]
            amazon_url = region["amazon_url"]

            region_fba_ids = set(all_regions_data.get(region_name, {}).keys())
            region_shipments_raw = {fba: shipments_raw[fba] for fba in region_fba_ids if fba in shipments_raw}

            if not region_shipments_raw:
                print(f"\n[{region_name}] No shipments to process — skipping.")
                write_region_summary(region_name, [], config["logs_folder"], ts_run)
                continue

            # Load persistent done list — skip FBAs we've already confirmed done in a previous run
            done_cache_file = Path(config["logs_folder"]) / f"completed_fba_{region_name}.txt"
            cached_done = set()
            if done_cache_file.exists() and not args.rewrite:
                cached_done = {line.strip() for line in done_cache_file.read_text(encoding="utf-8").splitlines() if line.strip()}
                before = len(region_shipments_raw)
                region_shipments_raw = {fba: v for fba, v in region_shipments_raw.items() if fba not in cached_done}
                skipped = before - len(region_shipments_raw)
                if skipped:
                    print(f"\n[{region_name}] Skipping {skipped} FBA(s) from done cache — already confirmed complete.")

            if not region_shipments_raw:
                print(f"\n[{region_name}] All shipments already in done cache — nothing to do.")
                write_region_summary(region_name, [], config["logs_folder"], ts_run)
                continue

            print(f"\n{'='*60}")
            print(f"[{region_name}] {len(region_shipments_raw)} shipment(s) — {amazon_url}")
            print(f"{'='*60}")

            # Login to this region's Amazon
            logged_in = wait_for_login(page, region_name, amazon_url, timeout_seconds=300)
            if not logged_in:
                write_region_summary(region_name, [], config["logs_folder"], ts_run)
                continue

            region_config = dict(config)
            region_config["amazon_base_url"] = amazon_url

            # Pre-check Amazon: skip shipments already complete (unless --rewrite forces all)
            if not args.rewrite:
                print(f"\n[{region_name}] Pre-checking Amazon status ({len(region_shipments_raw)} shipment(s))...")
                needs_upload, already_complete = check_all_shipments_on_amazon(region_shipments_raw, region_config, page)
                if already_complete:
                    print(f"[{region_name}] {len(already_complete)} already complete — skipping carrier scrape for those.")
                    # Persist newly confirmed done FBAs so we never check them again
                    all_done = cached_done | set(already_complete)
                    done_cache_file.write_text("\n".join(sorted(all_done)), encoding="utf-8")
                    logger.info(f"[{region_name}] Done cache updated: {len(all_done)} total FBA(s) in {done_cache_file.name}")
                region_shipments_raw = needs_upload  # dict {fba_id: entries}, already filtered
                if not region_shipments_raw:
                    print(f"[{region_name}] All shipments already complete — nothing to upload.")
                    write_region_summary(region_name, [], config["logs_folder"], ts_run)
                    continue
                print(f"[{region_name}] {len(region_shipments_raw)} shipment(s) need tracking upload.")

            # Carrier scraping — only for shipments that need uploading
            if args.skip_carrier:
                print(f"\n[{region_name}] Using main tracking numbers directly (--skip-carrier)...")
                shipments_with_subs = {}
                for fba_id, entries in region_shipments_raw.items():
                    main_ids = [e["tracking"] for e in entries if e.get("tracking")]
                    logger.info(f"FBA {fba_id}: using {len(main_ids)} main tracking number(s) directly")
                    shipments_with_subs[fba_id] = main_ids
            else:
                has_fedex = any(
                    "fedex" in str(e.get("carrier", "")).lower()
                    for entries in region_shipments_raw.values()
                    for e in entries
                )
                if has_fedex:
                    print(f"\n[{region_name}] Checking FedEx login...")
                    check_fedex_login(page)

                print(f"\n[{region_name}] Fetching sub-package tracking IDs from UPS/FedEx...")
                shipments_with_subs = {}
                for fba_id, entries in region_shipments_raw.items():
                    logger.info(f"\nFBA {fba_id}: {len(entries)} main tracking entries")
                    main_ids = [e["tracking"] for e in entries if e.get("tracking")]
                    sub_ids = get_all_sub_tracking(page, entries, config["logs_folder"])
                    all_ids = list(dict.fromkeys(main_ids + sub_ids))
                    logger.info(f"  -> {len(all_ids)} total tracking IDs ({len(main_ids)} main + {len(sub_ids)} sub)")
                    shipments_with_subs[fba_id] = all_ids

            # Save tracking IDs to JSON
            ts_ids = datetime.now().strftime("%Y%m%d_%H%M%S")
            tracking_ids_file = Path(config["logs_folder"]) / f"tracking_ids_{region_name}_{ts_ids}.json"
            combined = {
                fba_id: {
                    "parent": region_shipments_raw.get(fba_id, []),
                    "sub_ids": shipments_with_subs.get(fba_id, []),
                }
                for fba_id in shipments_with_subs
            }
            with open(tracking_ids_file, "w", encoding="utf-8") as f:
                json.dump(combined, f, indent=2)
            logger.info(f"Tracking IDs saved to: {tracking_ids_file}")
            print(f"\nTracking IDs saved to: {tracking_ids_file.name}")

            # Collect-only: print summary and skip upload for this region
            if args.collect_only:
                print(f"\n[{region_name}] Tracking collection complete (not uploading):")
                for fba_id, data in sorted(combined.items()):
                    main = [e["tracking"] for e in data.get("parent", []) if e.get("tracking")]
                    subs = data.get("sub_ids", [])
                    print(f"  {fba_id}")
                    print(f"    Main : {', '.join(main) if main else '(none)'}")
                    print(f"    Subs : {', '.join(subs) if subs else '(none)'}")
                continue

            # Upload
            print(f"\n[{region_name}] Uploading to Amazon...")
            region_results = upload_all_shipments(shipments_with_subs, region_config, page, force=args.rewrite)
            all_results.extend(region_results)
            write_region_summary(region_name, region_results, config["logs_folder"], ts_run)

            # Add successfully uploaded FBAs to done cache
            if not args.rewrite:
                newly_done = {r["fba_id"] for r in region_results if r["status"] in ("success", "skipped")}
                if newly_done:
                    all_done = cached_done | set(already_complete) | newly_done
                    done_cache_file.write_text("\n".join(sorted(all_done)), encoding="utf-8")
                    logger.info(f"[{region_name}] Done cache updated after upload: {len(all_done)} total FBA(s)")

        if args.collect_only:
            print(f"\n{'='*60}")
            print("TRACKING COLLECTION COMPLETE (nothing uploaded)")
            print(f"{'='*60}")
            return

        results = all_results

        # Step 4: Post-run - highlight updated rows, save to output, write combined summary
        ts_out = datetime.now().strftime("%Y%m%d_%H%M%S")
        input_folder = Path(config["input_folder"])
        output_folder = Path(config["output_folder"])
        updated_rows = collect_updated_row_numbers(shipments_all, results)

        for pattern in ["*.xls", "*.xlsx"]:
            for src_file in input_folder.glob(pattern):
                dest_file = output_folder / f"{src_file.stem}_processed_{ts_out}.xlsx"
                actual_dest = highlight_and_save(str(src_file), str(dest_file), updated_rows)
                if Path(actual_dest).exists():
                    try:
                        src_file.unlink()
                        logger.info(f"Highlighted output saved: {actual_dest}")
                        print(f"Output saved with highlights: {Path(actual_dest).name}")
                    except PermissionError:
                        logger.warning(f"Could not delete input file (in use): {src_file.name}")
                        print(f"Output saved: {Path(actual_dest).name} (input file still open — close it manually)")
                else:
                    logger.warning(f"Output file not confirmed — input NOT deleted: {src_file}")
                    print(f"WARNING: Could not confirm output file — input kept: {src_file.name}")

        write_summary(results, config["logs_folder"])

    finally:
        try:
            context.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass

    try:
        input("\nPress Enter to close this window...")
    except EOFError:
        pass


if __name__ == "__main__":
    main()
