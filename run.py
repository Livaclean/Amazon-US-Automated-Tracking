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
        "us_fc_codes_file": "us_fc_codes.txt",
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
    args = parser.parse_args()

    # Pre-initialize so these are always in scope even if an early exception occurs
    results = []
    shipments_all = {}

    config = load_config(args.config)
    ensure_folders(config)
    setup_logging(config["logs_folder"])

    # Import project modules after logging is configured
    from parse_excel import parse_and_filter, categorize_shipments
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

    # Step 1: Parse Excel
    logger.info("Reading Excel file from input folder...")
    shipments_all = parse_and_filter(config)

    if args.only_fba:
        if args.only_fba not in shipments_all:
            print(f"\nERROR: FBA ID '{args.only_fba}' not found in Excel.")
            return
        shipments_all = {args.only_fba: shipments_all[args.only_fba]}
        print(f"\nRunning for single shipment: {args.only_fba}")

    if args.fba_list:
        fba_list_path = Path(args.fba_list)
        if not fba_list_path.exists():
            print(f"\nERROR: FBA list file not found: {args.fba_list}")
            return
        fba_ids = {line.strip() for line in fba_list_path.read_text(encoding="utf-8").splitlines() if line.strip()}
        shipments_all = {fba: v for fba, v in shipments_all.items() if fba in fba_ids}
        print(f"\nFiltered to {len(shipments_all)} FBA(s) from list: {fba_list_path.name}")

    if not shipments_all:
        print(f"\nNo US FBA shipments found.")
        print(f"  - Drop your Excel file in:  {config['input_folder']}")
        print(f"  - Check column D has US FC codes (e.g. BNA, PHX, IND)")
        print(f"  - Check us_fc_codes.txt has the right prefixes")
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
        return

    # Categorize: split into has-tracking vs missing-tracking (also filters "/" entries)
    shipments_raw, missing_tracking = categorize_shipments(shipments_all)
    write_shipment_records(shipments_raw, missing_tracking, config["logs_folder"])

    if missing_tracking:
        print(f"\n  {len(missing_tracking)} FBA(s) have no usable tracking in Excel — recorded to logs.")

    total_main = sum(len(v) for v in shipments_raw.values())
    print(f"\nFound {len(shipments_raw)} US FBA shipments with {total_main} trackable entries.")

    # --from-json: skip Excel + carrier scraping, load tracking directly from JSON
    if args.from_json:
        from upload_tracking import get_slot_count
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

                    # Distribute pool sequentially based on slot counts
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
            results = upload_all_shipments(shipments_with_subs, config, page)
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

    # Initialize results before the try block so it is always defined,
    # even if an exception occurs before upload_all_shipments() is reached.
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
            print("\n[CHECK ONLY] Logging into Amazon Seller Central...")
            check_login_status(page, config["amazon_base_url"])
            print(f"\nChecking {len(shipments_raw)} shipments on Amazon (read-only)...\n")
            needs_upload, already_complete = check_all_shipments_on_amazon(shipments_raw, config, page)
            ts_chk = datetime.now().strftime("%Y%m%d_%H%M%S")
            logs = Path(config["logs_folder"])
            complete_file = logs / f"amazon_already_complete_{ts_chk}.txt"
            pending_file = logs / f"amazon_needs_upload_{ts_chk}.txt"
            complete_file.write_text("\n".join(sorted(already_complete)), encoding="utf-8")
            pending_file.write_text("\n".join(sorted(needs_upload.keys())), encoding="utf-8")
            print(f"\n{'='*60}")
            print(f"CHECK COMPLETE (nothing was uploaded)")
            print(f"  Already have tracking on Amazon : {len(already_complete)}")
            print(f"  Missing tracking (needs upload) : {len(needs_upload)}")
            print(f"\n  Already complete -> {complete_file.name}")
            print(f"  Needs upload     -> {pending_file.name}")
            print(f"{'='*60}")
            return

        # Step 3: Fetch sub-tracking IDs from carrier sites (or use main tracking numbers directly)
        if args.skip_carrier:
            print("\n[1/2] Skipping carrier scraping — using main tracking numbers directly...")
            shipments_with_subs = {}
            for fba_id, entries in shipments_raw.items():
                main_ids = [e["tracking"] for e in entries if e.get("tracking")]
                logger.info(f"FBA {fba_id}: using {len(main_ids)} main tracking number(s) directly")
                shipments_with_subs[fba_id] = main_ids
        else:
            # Check if any FedEx shipments present — if so, ensure FedEx login
            has_fedex = any(
                "fedex" in str(e.get("carrier", "")).lower()
                for entries in shipments_raw.values()
                for e in entries
            )
            if has_fedex:
                print("\n[FedEx] Checking FedEx login...")
                check_fedex_login(page)

            print("\n[1/2] Fetching sub-package tracking IDs from UPS/FedEx...")
            shipments_with_subs = {}
            for fba_id, entries in shipments_raw.items():
                logger.info(f"\nFBA {fba_id}: {len(entries)} main tracking entries")
                main_ids = [e["tracking"] for e in entries if e.get("tracking")]
                sub_ids = get_all_sub_tracking(page, entries, config["logs_folder"])
                # Include main tracking + sub-IDs, deduplicated, main first
                all_ids = list(dict.fromkeys(main_ids + sub_ids))
                logger.info(f"  -> {len(all_ids)} total tracking IDs ({len(main_ids)} main + {len(sub_ids)} sub)")
                shipments_with_subs[fba_id] = all_ids

        # Save all tracking IDs (parent + sub) to file for future reference
        ts_ids = datetime.now().strftime("%Y%m%d_%H%M%S")
        tracking_ids_file = Path(config["logs_folder"]) / f"tracking_ids_{ts_ids}.json"
        combined = {
            fba_id: {
                "parent": shipments_raw.get(fba_id, []),
                "sub_ids": shipments_with_subs.get(fba_id, []),
            }
            for fba_id in set(list(shipments_raw.keys()) + list(shipments_with_subs.keys()))
        }
        with open(tracking_ids_file, "w", encoding="utf-8") as f:
            json.dump(combined, f, indent=2)
        logger.info(f"Tracking IDs saved to: {tracking_ids_file}")
        print(f"\nTracking IDs saved to: {tracking_ids_file}")

        # Collect-only mode: print tracking list and stop before uploading
        if args.collect_only:
            print(f"\n{'='*60}")
            print("TRACKING COLLECTION COMPLETE (nothing uploaded)")
            print(f"{'='*60}")
            for fba_id, data in sorted(combined.items()):
                main = [e["tracking"] for e in data.get("parent", []) if e.get("tracking")]
                subs = data.get("sub_ids", [])
                print(f"\n  {fba_id}")
                print(f"    Main : {', '.join(main) if main else '(none)'}")
                print(f"    Subs : {', '.join(subs) if subs else '(none)'}")
            print(f"\n{'='*60}")
            return

        # Step 4: Upload to Amazon Seller Central
        print("\n[2/2] Uploading to Amazon Seller Central...")
        check_login_status(page, config["amazon_base_url"])
        results = upload_all_shipments(shipments_with_subs, config, page)

        # Step 5: Post-run - highlight updated rows, save to output, write summary
        # (inside try so files are only processed when upload completed without crashing)
        ts_out = datetime.now().strftime("%Y%m%d_%H%M%S")
        input_folder = Path(config["input_folder"])
        output_folder = Path(config["output_folder"])
        # Use shipments_all (full dict with row_number per entry) not shipments_raw (tracking-filtered subset)
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
                        logger.warning(f"Could not delete input file (in use): {src_file.name} — please close it manually")
                        print(f"Output saved: {Path(actual_dest).name} (input file still open — close it to delete)")
                else:
                    logger.warning(f"Output file not confirmed — input NOT deleted: {src_file}")
                    print(f"WARNING: Could not confirm output file — input kept: {src_file.name}")

        write_summary(results, config["logs_folder"])

    finally:
        context.close()
        pw.stop()

    try:
        input("\nPress Enter to close this window...")
    except EOFError:
        pass


if __name__ == "__main__":
    main()
