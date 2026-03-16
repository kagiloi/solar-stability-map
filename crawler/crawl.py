#!/usr/bin/env python3
"""JMA Solar Radiation Crawler - Main entry point.

Usage:
    python -m crawler.crawl --discover    # Step 1: discover stations
    python -m crawler.crawl --download    # Step 2: download CSVs
    python -m crawler.crawl --parse       # Step 3: parse CSVs into SQLite
    python -m crawler.crawl --verify      # Step 4: run verification checks
    python -m crawler.crawl               # runs all steps sequentially
"""

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(description="JMA Solar Radiation Crawler")
    parser.add_argument("--discover", action="store_true", help="Discover stations")
    parser.add_argument("--download", action="store_true", help="Download CSVs")
    parser.add_argument("--parse", action="store_true", help="Parse CSVs into SQLite")
    parser.add_argument("--verify", action="store_true", help="Run verification checks")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    run_all = not (args.discover or args.download or args.parse or args.verify)

    if args.discover or run_all:
        from crawler.stations import discover_stations

        logging.info("=== Step 1: Discovering stations ===")
        stations = discover_stations()
        logging.info("Discovered %d stations with solar radiation data.", len(stations))

    if args.download or run_all:
        from crawler.downloader import download_all

        logging.info("=== Step 2: Downloading CSVs ===")
        success, failure = download_all()
        logging.info("Download results: %d success, %d failed.", success, failure)
        if failure > 0:
            logging.warning("Some downloads failed. Re-run --download to retry.")

    if args.parse or run_all:
        from crawler.parser import parse_all

        logging.info("=== Step 3: Parsing CSVs into SQLite ===")
        stations_count, rows_count = parse_all()
        logging.info("Parsed %d stations, %d total rows.", stations_count, rows_count)

    if args.verify or run_all:
        from crawler.parser import verify

        logging.info("=== Step 4: Verification ===")
        verify()

    logging.info("Done.")


if __name__ == "__main__":
    main()
