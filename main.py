#!/usr/bin/env python3
"""
Bounce (usebounce.com) competitor pricing scraper.

Usage:
    python main.py --city "London"
    python main.py --city "London" --city "Paris"
    python main.py --all-cities
    python main.py --city "London" --headless false
    python main.py --city "London" --max-locations 5
"""
import argparse
import asyncio
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from scraper.browser import new_page, new_page_for_computer_use
from scraper.computer_use import scrape_city_computer_use
from scraper.extractor import scrape_city
from scraper.export import to_csv
from scraper.models import PriceRecord


def load_config(path: str = "config/targets.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


async def run_computer_use(
    cities: list[str],
    headless: bool,
    max_locations: int,
) -> list[PriceRecord]:
    all_records: list[PriceRecord] = []

    async with new_page_for_computer_use(headless=headless) as page:
        for city in cities:
            print(f"\n{'='*60}")
            print(f"  Scraping (Computer Use): {city}")
            print(f"{'='*60}")
            try:
                records = await scrape_city_computer_use(
                    page=page,
                    city=city,
                    max_locations=max_locations,
                )
                all_records.extend(records)
                print(f"  → {len(records)} price record(s) collected for {city}")
            except Exception as e:
                print(f"  [ERROR] Failed to scrape {city}: {e}")

    return all_records


async def run(cities: list[str], headless: bool, delay: float, max_locations: int) -> list[PriceRecord]:
    all_records: list[PriceRecord] = []

    async with new_page(headless=headless) as page:
        for city in cities:
            print(f"\n{'='*60}")
            print(f"  Scraping: {city}")
            print(f"{'='*60}")
            try:
                records = await scrape_city(
                    page=page,
                    city=city,
                    delay=delay,
                    max_locations=max_locations,
                )
                all_records.extend(records)
                print(f"  → {len(records)} price record(s) collected for {city}")
            except Exception as e:
                print(f"  [ERROR] Failed to scrape {city}: {e}")

    return all_records


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Scrape Bounce competitor pricing")
    parser.add_argument(
        "--city",
        action="append",
        dest="cities",
        metavar="CITY",
        help="City to scrape (can be repeated: --city London --city Paris)",
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
        help="Max locations per city (0 = unlimited)",
    )
    parser.add_argument(
        "--mode",
        choices=["playwright", "computer-use"],
        default="playwright",
        help="Scraping mode: playwright (default) uses API/DOM tiers; computer-use uses Claude visual AI",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for CSV output (default: output/)",
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

    print(f"Target: {config['competitor']['name']} ({config['competitor']['url']})")
    print(f"Cities: {', '.join(cities)}")
    if mode == "computer-use":
        print(f"Mode: computer-use | Headless: {headless} | Max locations: {max_locs or 'unlimited'}")
        records = asyncio.run(run_computer_use(cities, headless=headless, max_locations=max_locs))
    else:
        print(f"Mode: playwright | Headless: {headless} | Delay: {delay}s | Max locations: {max_locs or 'unlimited'}")
        records = asyncio.run(run(cities, headless=headless, delay=delay, max_locations=max_locs))

    city_label = cities[0] if len(cities) == 1 else ""
    to_csv(records, output_dir=args.output_dir, city=city_label)

    # Print summary table to stdout
    if records:
        print("\n--- Summary ---")
        print(f"{'City':<15} {'Location':<30} {'Size':<12} {'Price':>8} {'Currency':<6}")
        print("-" * 75)
        for r in records:
            print(f"{r.city:<15} {r.location_name[:29]:<30} {r.size:<12} {r.price:>8.2f} {r.currency:<6}")


if __name__ == "__main__":
    main()
