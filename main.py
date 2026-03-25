#!/usr/bin/env python3
"""
Competitor pricing scraper — Bounce, Stasher, Radical Storage.

Usage:
    python main.py --city "London"
    python main.py --city "London" --city "Paris"
    python main.py --all-cities
    python main.py --city "London" --headless false
    python main.py --city "London" --max-locations 5
    python main.py --city "London" --mode computer-use
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

from scraper.browser import new_page, new_page_for_computer_use
from scraper.computer_use import scrape_city_computer_use
from scraper import extractor as bounce
from scraper import stasher
from scraper import radical
from scraper.export import to_csv
from scraper.dashboard import generate_dashboard
from scraper.models import PriceRecord


def load_config(path: str = "config/targets.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Parallel playwright run — all 3 companies
# ---------------------------------------------------------------------------

async def run(
    cities: list[str],
    headless: bool,
    delay: float,
    max_locations: int,
) -> tuple[list[PriceRecord], list[dict]]:
    """Run Bounce, Stasher, Radical Storage scrapers in parallel for each city."""
    all_records: list[PriceRecord] = []
    all_logs: list[dict] = []

    for city in cities:
        print(f"\n{'='*60}")
        print(f"  Scraping: {city}")
        print(f"{'='*60}")

        city_records, city_logs = await _scrape_city_all_companies(
            city, headless, delay, max_locations
        )
        all_records.extend(city_records)
        all_logs.extend(city_logs)

    return all_records, all_logs


async def _scrape_city_all_companies(
    city: str,
    headless: bool,
    delay: float,
    max_locations: int,
) -> tuple[list[PriceRecord], list[dict]]:
    """Run all 3 company scrapers concurrently for a single city."""
    records: list[PriceRecord] = []
    logs: list[dict] = []

    async def run_company(module, company_name: str) -> None:
        t0 = time.monotonic()
        log: dict = {
            "company": company_name,
            "status": "ok",
            "records_count": 0,
            "duration_sec": 0.0,
            "error_message": None,
        }
        try:
            # Pass headless flag to stasher so it can choose browser vs sitemap strategy
            extra_kwargs = {"headless": headless} if company_name == "Stasher" else {}
            async with new_page(headless=headless) as page:
                result = await module.scrape_city(
                    page=page,
                    city=city,
                    delay=delay,
                    max_locations=max_locations,
                    **extra_kwargs,
                )
            if result:
                records.extend(result)
                log["records_count"] = len(result)
                log["status"] = "ok"
                print(f"  [{company_name}] → {len(result)} record(s)")
            else:
                log["status"] = "no_results"
                print(f"  [{company_name}] → 0 records (no data found)")
        except Exception as exc:
            log["status"] = "error"
            log["error_message"] = str(exc)
            print(f"  [{company_name}] → ERROR: {exc}")
        finally:
            log["duration_sec"] = round(time.monotonic() - t0, 1)
            logs.append(log)

    await asyncio.gather(
        run_company(bounce, "Bounce"),
        run_company(stasher, "Stasher"),
        run_company(radical, "Radical Storage"),
    )

    return records, logs


# ---------------------------------------------------------------------------
# Computer Use mode — Bounce only (visual AI)
# ---------------------------------------------------------------------------

async def run_computer_use(
    cities: list[str],
    headless: bool,
    max_locations: int,
) -> tuple[list[PriceRecord], list[dict]]:
    all_records: list[PriceRecord] = []
    all_logs: list[dict] = []

    async with new_page_for_computer_use(headless=headless) as page:
        for city in cities:
            print(f"\n{'='*60}")
            print(f"  Scraping (Computer Use): {city}")
            print(f"{'='*60}")
            t0 = time.monotonic()
            log: dict = {
                "company": "Bounce",
                "status": "ok",
                "records_count": 0,
                "duration_sec": 0.0,
                "error_message": None,
            }
            try:
                records = await scrape_city_computer_use(
                    page=page,
                    city=city,
                    max_locations=max_locations,
                )
                all_records.extend(records)
                log["records_count"] = len(records)
                log["status"] = "ok" if records else "no_results"
                print(f"  → {len(records)} price record(s) collected for {city}")
            except Exception as e:
                log["status"] = "error"
                log["error_message"] = str(e)
                print(f"  [ERROR] Failed to scrape {city}: {e}")
            finally:
                log["duration_sec"] = round(time.monotonic() - t0, 1)
                all_logs.append(log)

    return all_records, all_logs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Scrape competitor pricing: Bounce, Stasher, Radical Storage"
    )
    parser.add_argument(
        "--city",
        action="append",
        dest="cities",
        metavar="CITY",
        help="City to scrape (repeatable: --city London --city Paris)",
    )
    parser.add_argument(
        "--all-cities",
        action="store_true",
        help="Scrape all cities defined in config/targets.yaml",
    )
    parser.add_argument(
        "--headless",
        type=lambda x: x.lower() not in ("false", "0", "no"),
        default=True,
        metavar="true|false",
        help="Run browser in headless mode (default: true)",
    )
    parser.add_argument(
        "--max-locations",
        type=int,
        default=0,
        metavar="N",
        help="Max locations per city per company (0 = unlimited)",
    )
    parser.add_argument(
        "--mode",
        choices=["playwright", "computer-use"],
        default="playwright",
        help="Scraping mode: playwright (default) or computer-use (Claude visual AI, Bounce only)",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for CSV + dashboard output (default: output/)",
    )
    args = parser.parse_args()

    config = load_config()
    scraper_cfg = config.get("scraper", {})

    delay = scraper_cfg.get("delay_between_locations", 2)
    max_locs = args.max_locations or scraper_cfg.get("max_locations_per_city", 0)
    headless = args.headless
    mode = args.mode

    if args.all_cities:
        cities = config.get("cities", [])
    elif args.cities:
        cities = args.cities
    else:
        parser.print_help()
        print("\nError: specify --city or --all-cities")
        sys.exit(1)

    if not cities:
        print("No cities configured. Add cities to config/targets.yaml or use --city.")
        sys.exit(1)

    companies = "Bounce only (computer-use)" if mode == "computer-use" else "Bounce + Stasher + Radical Storage"
    print(f"Companies: {companies}")
    print(f"Cities:    {', '.join(cities)}")
    print(f"Mode:      {mode} | Headless: {headless} | Max locations: {max_locs or 'unlimited'}")

    if mode == "computer-use":
        records, logs = asyncio.run(
            run_computer_use(cities, headless=headless, max_locations=max_locs)
        )
    else:
        records, logs = asyncio.run(
            run(cities, headless=headless, delay=delay, max_locations=max_locs)
        )

    city_label = cities[0] if len(cities) == 1 else ""

    # Export CSV
    to_csv(records, output_dir=args.output_dir, city=city_label)

    # Generate HTML dashboard
    generate_dashboard(records, logs, city=city_label or "all cities", output_dir=args.output_dir)

    # Print summary table to stdout
    if records:
        print("\n--- Summary ---")
        print(f"{'Company':<18} {'City':<12} {'Location':<28} {'Size':<12} {'Price':>8} {'Currency':<6}")
        print("-" * 90)
        for r in records:
            print(
                f"{r.company:<18} {r.city:<12} {r.location_name[:27]:<28} "
                f"{r.size:<12} {r.price:>8.2f} {r.currency:<6}"
            )

    # Print log summary
    print("\n--- Scrape Log ---")
    for log in logs:
        status_icon = {"ok": "✅", "no_results": "⚠️ ", "error": "❌"}.get(log["status"], "?")
        err = f" — {log['error_message']}" if log["error_message"] else ""
        print(
            f"  {status_icon} {log['company']:<20} {log['records_count']:>3} records  "
            f"{log['duration_sec']:.1f}s{err}"
        )


if __name__ == "__main__":
    main()
