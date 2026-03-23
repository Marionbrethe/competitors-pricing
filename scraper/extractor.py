"""
Bounce (usebounce.com) pricing extractor.

Strategy:
  1. Intercept XHR/fetch responses to find an internal JSON API for locations/prices.
  2. If API data is found, parse it directly (fast, reliable).
  3. Fall back to DOM scraping: search city → iterate location cards → open each →
     parse the bag-size price panel.

The scraper collects: city, location_name, address, size, price, currency, price_unit.
"""
import asyncio
import re
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import Page, Response

from scraper.models import PriceRecord


BASE_URL = "https://www.usebounce.com"
CITY_PAGE_TEMPLATE = BASE_URL + "/luggage-storage/{slug}"

# Currency symbol → ISO code mapping
CURRENCY_MAP = {
    "£": "GBP",
    "$": "USD",
    "€": "EUR",
    "¥": "JPY",
    "A$": "AUD",
    "C$": "CAD",
}

# Regex to extract price and currency from strings like "£15.00 / day"
_PRICE_RE = re.compile(
    r"([£$€¥]|A\$|C\$)\s*([\d,]+(?:\.\d+)?)\s*/\s*(\w+)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# API interception
# ---------------------------------------------------------------------------

class _ApiCollector:
    """Accumulates JSON responses that look like location/pricing data."""

    def __init__(self) -> None:
        self.locations: list[dict[str, Any]] = []

    async def handle_response(self, response: Response) -> None:
        url = response.url
        # Bounce likely calls an internal search/availability API
        if not any(kw in url for kw in ("storage", "location", "search", "venue", "spot")):
            return
        if response.status != 200:
            return
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            return
        try:
            body = await response.json()
            if isinstance(body, list) and body:
                self.locations.extend(body)
            elif isinstance(body, dict):
                # Try common wrapper keys
                for key in ("data", "results", "locations", "items", "venues", "spots"):
                    if key in body and isinstance(body[key], list):
                        self.locations.extend(body[key])
                        break
        except Exception:
            pass


def _parse_api_locations(raw: list[dict], city: str, scraped_at: datetime) -> list[PriceRecord]:
    """
    Attempt to extract PriceRecords from raw JSON location objects.
    Field names are guesses based on common API conventions.
    """
    records: list[PriceRecord] = []

    for loc in raw:
        name = (
            loc.get("name")
            or loc.get("title")
            or loc.get("storeName")
            or loc.get("venue_name")
            or "Unknown"
        )
        address = (
            loc.get("address")
            or loc.get("full_address")
            or loc.get("formattedAddress")
            or ""
        )
        pricing = (
            loc.get("pricing")
            or loc.get("prices")
            or loc.get("bagPrices")
            or {}
        )
        if not pricing:
            continue

        # pricing may be a dict like {"small": 15.0, "regular": 15.0, "oversized": 15.0}
        # or a list of objects
        if isinstance(pricing, dict):
            for size, price in pricing.items():
                if isinstance(price, (int, float)):
                    records.append(PriceRecord(
                        company="Bounce",
                        city=city,
                        location_name=str(name),
                        address=str(address),
                        size=size,
                        price=float(price),
                        currency="GBP",  # will be refined if currency field exists
                        price_unit="day",
                        scraped_at=scraped_at,
                    ))
        elif isinstance(pricing, list):
            for item in pricing:
                if isinstance(item, dict):
                    size = item.get("size") or item.get("type") or item.get("bagSize") or ""
                    price = item.get("price") or item.get("amount") or 0
                    currency = item.get("currency") or item.get("currencyCode") or "GBP"
                    if size and price:
                        records.append(PriceRecord(
                            company="Bounce",
                            city=city,
                            location_name=str(name),
                            address=str(address),
                            size=str(size),
                            price=float(price),
                            currency=currency,
                            price_unit="day",
                            scraped_at=scraped_at,
                        ))

    return records


# ---------------------------------------------------------------------------
# DOM scraping helpers
# ---------------------------------------------------------------------------

def _parse_price_text(text: str) -> tuple[float, str, str]:
    """
    Parse '£15.00 / day' → (15.0, 'GBP', 'day').
    Returns (0.0, '', '') if no match.
    """
    m = _PRICE_RE.search(text)
    if not m:
        return 0.0, "", ""
    symbol, amount_str, unit = m.group(1), m.group(2), m.group(3)
    amount = float(amount_str.replace(",", ""))
    currency = CURRENCY_MAP.get(symbol, symbol)
    return amount, currency, unit.lower()


def _city_to_slug(city: str) -> str:
    """Convert 'New York' → 'new-york' for URL paths."""
    return city.strip().lower().replace(" ", "-")


async def _navigate_to_city(page: Page, city: str, delay: float) -> bool:
    """
    Try direct URL navigation to the city page.
    Returns True if the page loaded and contains location results.
    """
    slug = _city_to_slug(city)
    url = CITY_PAGE_TEMPLATE.format(slug=slug)
    print(f"  Trying direct URL: {url}")
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if resp and resp.status >= 400:
            print(f"  Direct URL returned HTTP {resp.status} — will try search")
            return False
        await asyncio.sleep(delay)
        return True
    except Exception as e:
        print(f"  Direct URL failed: {e}")
        return False


async def _search_for_city(page: Page, city: str, delay: float) -> bool:
    """
    Navigate to the homepage and use the search box to find the city.
    Returns True if a search was successfully submitted.
    """
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(2)

    search_selectors = [
        'input[placeholder*="city" i]',
        'input[placeholder*="location" i]',
        'input[placeholder*="where" i]',
        'input[placeholder*="search" i]',
        'input[placeholder*="store" i]',
        'input[placeholder*="address" i]',
        'input[placeholder*="destination" i]',
        'input[type="search"]',
        'input[name="search"]',
        'input[name="location"]',
        'input[name="q"]',
        '[data-testid*="search"] input',
        '[class*="search"] input',
        '[class*="Search"] input',
        'form input[type="text"]',
    ]
    search_input = None
    for sel in search_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1_500):
                search_input = el
                print(f"  Found search input via: {sel}")
                break
        except Exception:
            continue

    if search_input is None:
        print(f"  [!] Could not find search input on {BASE_URL} for city '{city}'")
        return False

    await search_input.click()
    await search_input.fill(city)
    await asyncio.sleep(1)

    suggestion_selectors = [
        '[role="option"]',
        '[class*="suggestion"]',
        '[class*="autocomplete"] li',
        '[class*="dropdown"] li',
        '[data-testid*="suggestion"]',
        'ul[class*="search"] li',
        'ul[class*="result"] li',
    ]
    suggestion_clicked = False
    for sel in suggestion_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2_000):
                await el.click()
                suggestion_clicked = True
                break
        except Exception:
            continue

    if not suggestion_clicked:
        await search_input.press("Enter")

    await asyncio.sleep(delay)
    return True


async def _dom_scrape_city(page: Page, city: str, delay: float, max_locs: int) -> list[PriceRecord]:
    """Full DOM scraping fallback for one city."""
    records: list[PriceRecord] = []
    scraped_at = datetime.now(timezone.utc)

    # 1. Try direct city URL first, fall back to search
    navigated = await _navigate_to_city(page, city, delay)
    if not navigated:
        ok = await _search_for_city(page, city, delay)
        if not ok:
            return records

    # 2. Collect location cards from the results list
    location_selectors = [
        '[data-testid*="location"]',
        '[data-testid*="store"]',
        '[data-testid*="venue"]',
        '[class*="location-card"]',
        '[class*="LocationCard"]',
        '[class*="store-card"]',
        '[class*="StoreCard"]',
        '[class*="venue-card"]',
        '[class*="VenueCard"]',
        '[class*="result-item"]',
        '[class*="ResultItem"]',
        '[class*="storage-location"]',
        '[class*="StorageLocation"]',
        'li[class*="listing"]',
        'li[class*="Listing"]',
        'article[class*="location"]',
        'article[class*="store"]',
        'a[href*="/luggage-storage/"]',
    ]
    location_elements = []
    for sel in location_selectors:
        els = page.locator(sel)
        count = await els.count()
        if count > 0:
            location_elements = [els.nth(i) for i in range(count)]
            print(f"  Found {count} locations using selector '{sel}'")
            break

    if not location_elements:
        print(f"  [!] No location cards found for '{city}' — DOM structure may have changed")
        return records

    if max_locs and len(location_elements) > max_locs:
        location_elements = location_elements[:max_locs]

    # 3. For each location: click it → parse the bag-size price panel
    for i, loc_el in enumerate(location_elements):
        try:
            loc_name = await _extract_text(loc_el, [
                '[class*="name"]', '[class*="title"]', 'h2', 'h3', 'strong',
            ])
            loc_address = await _extract_text(loc_el, [
                '[class*="address"]', '[class*="subtitle"]', 'p', 'span',
            ])
            await loc_el.click()
            await asyncio.sleep(delay)

            # The side panel / modal with bag sizes should now be visible
            size_records = await _extract_bag_sizes(page, city, loc_name, loc_address, scraped_at)
            if size_records:
                records.extend(size_records)
                print(f"  [{i+1}] {loc_name}: {len(size_records)} size(s) scraped")
            else:
                print(f"  [{i+1}] {loc_name}: no pricing found in panel")

            await asyncio.sleep(delay)

        except Exception as e:
            print(f"  [!] Error on location #{i+1}: {e}")
            continue

    return records


async def _extract_text(parent, selectors: list[str]) -> str:
    """Try selectors in order and return the first non-empty text found."""
    for sel in selectors:
        try:
            el = parent.locator(sel).first
            text = await el.inner_text(timeout=1_000)
            text = text.strip()
            if text:
                return text
        except Exception:
            continue
    return ""


async def _extract_bag_sizes(
    page: Page,
    city: str,
    loc_name: str,
    loc_address: str,
    scraped_at: datetime,
) -> list[PriceRecord]:
    """
    Extract bag-size pricing rows from the 'How many bags?' side panel.

    Expected structure (from the screenshot):
      - Row per size (Small / Regular / Odd-sized)
      - Each row contains a size label, description, and price like '£15.00 / day'
    """
    records: list[PriceRecord] = []

    # Selectors for the panel container
    panel_selectors = [
        '[class*="bag"]',
        '[class*="size"]',
        '[class*="pricing"]',
        '[class*="item-type"]',
        '[data-testid*="bag"]',
    ]

    # Find rows inside the panel
    row_selectors = [
        '[class*="bag-type"]',
        '[class*="size-row"]',
        '[class*="item-row"]',
        '[class*="pricing-row"]',
        'li[class*="bag"]',
    ]

    rows = []
    for sel in row_selectors:
        els = page.locator(sel)
        count = await els.count()
        if count > 0:
            rows = [els.nth(i) for i in range(count)]
            break

    if not rows:
        # Fallback: search for any element containing a price pattern in the panel area
        all_text = await page.inner_text("body")
        # Look for size + price patterns near each other
        size_price_re = re.compile(
            r"(Small|Regular|Odd-sized|Oversized)[^\n£$€]*([£$€]\s*[\d.]+\s*/\s*day)",
            re.IGNORECASE,
        )
        for m in size_price_re.finditer(all_text):
            size_text = m.group(1)
            price_text = m.group(2)
            price, currency, unit = _parse_price_text(price_text)
            if price > 0:
                records.append(PriceRecord(
                    company="Bounce",
                    city=city,
                    location_name=loc_name or "Unknown",
                    address=loc_address or "",
                    size=size_text,
                    price=price,
                    currency=currency or "GBP",
                    price_unit=unit or "day",
                    scraped_at=scraped_at,
                ))
        return records

    for row in rows:
        try:
            row_text = await row.inner_text(timeout=2_000)
            # Determine size label
            size = ""
            for label in ("Small", "Regular", "Odd-sized", "Oversized", "Large"):
                if label.lower() in row_text.lower():
                    size = label
                    break

            price, currency, unit = _parse_price_text(row_text)
            if size and price > 0:
                records.append(PriceRecord(
                    company="Bounce",
                    city=city,
                    location_name=loc_name or "Unknown",
                    address=loc_address or "",
                    size=size,
                    price=price,
                    currency=currency or "GBP",
                    price_unit=unit or "day",
                    scraped_at=scraped_at,
                ))
        except Exception:
            continue

    return records


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def scrape_city(
    page: Page,
    city: str,
    delay: float = 2.0,
    max_locations: int = 0,
) -> list[PriceRecord]:
    """
    Scrape pricing for all storage locations in *city* from Bounce.

    First attempts API interception; falls back to DOM scraping.
    """
    scraped_at = datetime.now(timezone.utc)
    collector = _ApiCollector()
    page.on("response", lambda r: asyncio.ensure_future(collector.handle_response(r)))

    slug = _city_to_slug(city)
    city_url = CITY_PAGE_TEMPLATE.format(slug=slug)
    print(f"[{city}] Navigating to {city_url} …")

    # Try direct city URL first; fall back to homepage search
    try:
        resp = await page.goto(city_url, wait_until="domcontentloaded", timeout=30_000)
        if resp and resp.status >= 400:
            print(f"[{city}] Direct URL returned HTTP {resp.status} — trying homepage search")
            await _search_for_city(page, city, delay)
    except Exception as e:
        print(f"[{city}] Direct URL failed ({e}) — trying homepage search")
        await _search_for_city(page, city, delay)

    await asyncio.sleep(delay)

    # Give API calls a moment to complete
    await asyncio.sleep(1)

    if collector.locations:
        print(f"[{city}] API interception succeeded — {len(collector.locations)} location(s) found")
        records = _parse_api_locations(collector.locations, city, scraped_at)
        if records:
            return records
        print(f"[{city}] API data found but price fields not recognised — falling back to DOM")

    print(f"[{city}] Falling back to DOM scraping …")
    return await _dom_scrape_city(page, city, delay, max_locations)
