"""
Stasher (stasher.com) pricing extractor.

Strategy:
  1. Intercept XHR/fetch responses to find an internal JSON API for stashpoints/prices.
  2. If API data is found, parse it directly (fast, reliable).
  3. Fall back to DOM scraping: navigate to city page → iterate location cards → parse prices.

URL pattern: https://stasher.com/luggage-storage/{country}/{city-slug}
Pricing model: flat rate — all bag sizes charged the same daily rate per location.
"""
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from playwright.async_api import Page, Response

from scraper.models import PriceRecord


BASE_URL = "https://stasher.com"
CITY_PAGE_TEMPLATE = BASE_URL + "/luggage-storage/{country}/{slug}"
CITY_PAGE_SIMPLE = BASE_URL + "/luggage-storage/{slug}"

# Currency symbol → ISO code mapping
CURRENCY_MAP = {
    "£": "GBP",
    "$": "USD",
    "€": "EUR",
    "¥": "JPY",
    "A$": "AUD",
    "C$": "CAD",
}

_PRICE_RE = re.compile(
    r"([£$€¥]|A\$|C\$)\s*([\d,]+(?:\.\d+)?)"
    r"\s*(?:/|per|a)?\s*(?:bag\s*(?:/|per)\s*)?(\w+)",
    re.IGNORECASE,
)

# Coordinates for major cities (used in direct API calls)
CITY_COORDS: dict[str, tuple[float, float]] = {
    "london": (51.5074, -0.1278),
    "paris": (48.8566, 2.3522),
    "barcelona": (41.3851, 2.1734),
    "madrid": (40.4168, -3.7038),
    "rome": (41.9028, 12.4964),
    "milan": (45.4654, 9.1859),
    "amsterdam": (52.3676, 4.9041),
    "berlin": (52.5200, 13.4050),
    "lisbon": (38.7169, -9.1399),
    "prague": (50.0755, 14.4378),
    "vienna": (48.2082, 16.3738),
    "new york": (40.7128, -74.0060),
    "los angeles": (34.0522, -118.2437),
    "chicago": (41.8781, -87.6298),
    "san francisco": (37.7749, -122.4194),
    "tokyo": (35.6762, 139.6503),
    "sydney": (-33.8688, 151.2093),
}

# Curated city → country slug map for Stasher URL construction
CITY_TO_COUNTRY: dict[str, str] = {
    "marseille": "france",
    "paris": "france",
    "lyon": "france",
    "nice": "france",
    "bordeaux": "france",
    "toulouse": "france",
    "strasbourg": "france",
    "barcelona": "spain",
    "madrid": "spain",
    "seville": "spain",
    "valencia": "spain",
    "malaga": "spain",
    "london": "united-kingdom",
    "edinburgh": "united-kingdom",
    "manchester": "united-kingdom",
    "birmingham": "united-kingdom",
    "glasgow": "united-kingdom",
    "liverpool": "united-kingdom",
    "rome": "italy",
    "milan": "italy",
    "florence": "italy",
    "venice": "italy",
    "naples": "italy",
    "amsterdam": "netherlands",
    "rotterdam": "netherlands",
    "berlin": "germany",
    "munich": "germany",
    "hamburg": "germany",
    "cologne": "germany",
    "frankfurt": "germany",
    "lisbon": "portugal",
    "porto": "portugal",
    "prague": "czech-republic",
    "vienna": "austria",
    "brussels": "belgium",
    "zurich": "switzerland",
    "geneva": "switzerland",
    "new york": "united-states",
    "los angeles": "united-states",
    "chicago": "united-states",
    "san francisco": "united-states",
    "miami": "united-states",
    "tokyo": "japan",
    "osaka": "japan",
    "sydney": "australia",
    "melbourne": "australia",
    "toronto": "canada",
    "vancouver": "canada",
}


def _city_to_slug(city: str) -> str:
    """Convert 'New York' → 'new-york'."""
    import unicodedata
    s = city.strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace(" ", "-")


def _get_country(city: str) -> str:
    """Look up country for a city; fall back to 'unknown'."""
    return CITY_TO_COUNTRY.get(city.strip().lower(), "unknown")


# ---------------------------------------------------------------------------
# API interception
# ---------------------------------------------------------------------------

class _ApiCollector:
    """Accumulates JSON responses that look like stashpoint/pricing data."""

    def __init__(self) -> None:
        self.locations: list[dict[str, Any]] = []

    async def handle_response(self, response: Response) -> None:
        if response.status != 200:
            return
        if "json" not in response.headers.get("content-type", ""):
            return
        try:
            body = await response.json()
            if isinstance(body, list) and body and isinstance(body[0], dict):
                self.locations.extend(body)
            elif isinstance(body, dict):
                for key in ("data", "results", "locations", "stashpoints", "items", "venues", "spots"):
                    if key in body and isinstance(body[key], list) and body[key]:
                        self.locations.extend(body[key])
                        break
        except Exception:
            pass


def _parse_api_locations(raw: list[dict], city: str, scraped_at: datetime) -> list[PriceRecord]:
    records: list[PriceRecord] = []
    for loc in raw:
        name = (
            loc.get("name") or loc.get("title") or loc.get("storeName")
            or loc.get("venue_name") or loc.get("stashpoint_name") or "Unknown"
        )
        address = (
            loc.get("address") or loc.get("full_address")
            or loc.get("formattedAddress") or ""
        )
        # Stasher uses flat-rate pricing — look for a single daily price field
        price_val = (
            loc.get("price") or loc.get("dailyPrice") or loc.get("price_per_day")
            or loc.get("pricePerDay") or loc.get("rate") or 0
        )
        currency = (
            loc.get("currency") or loc.get("currencyCode") or loc.get("currency_code") or "GBP"
        )
        if not price_val:
            # Try nested pricing object
            pricing = loc.get("pricing") or loc.get("prices") or {}
            if isinstance(pricing, dict):
                price_val = (
                    pricing.get("daily") or pricing.get("perDay")
                    or pricing.get("price") or pricing.get("amount") or 0
                )

        if price_val and float(price_val) > 0:
            records.append(PriceRecord(
                company="Stasher",
                city=city,
                location_name=str(name),
                address=str(address),
                size="flat-rate",
                price=float(price_val),
                currency=str(currency).upper(),
                price_unit="day",
                scraped_at=scraped_at,
            ))
    return records


# ---------------------------------------------------------------------------
# DOM scraping helpers
# ---------------------------------------------------------------------------

def _parse_price_text(text: str) -> tuple[float, str, str]:
    """Parse '£5.99 bag/day' → (5.99, 'GBP', 'day'). Returns (0, '', '') on failure."""
    m = _PRICE_RE.search(text)
    if not m:
        return 0.0, "", ""
    symbol, amount_str, unit = m.group(1), m.group(2), m.group(3)
    amount = float(amount_str.replace(",", ""))
    currency = CURRENCY_MAP.get(symbol, symbol)
    return amount, currency, unit.lower()


async def _navigate_to_city(page: Page, city: str, delay: float) -> bool:
    slug = _city_to_slug(city)
    country = _get_country(city)
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    # Try with date param first (triggers price display), then without
    for url in [
        CITY_PAGE_SIMPLE.format(slug=slug) + f"?date={tomorrow}&bags=1",
        CITY_PAGE_TEMPLATE.format(country=country, slug=slug) + f"?date={tomorrow}&bags=1",
        CITY_PAGE_SIMPLE.format(slug=slug),
        CITY_PAGE_TEMPLATE.format(country=country, slug=slug),
    ]:
        print(f"  [Stasher] Trying direct URL: {url}")
        try:
            resp = await page.goto(url, wait_until="networkidle", timeout=45_000)
            if resp and resp.status < 400:
                await asyncio.sleep(delay)
                return True
            print(f"  [Stasher] {url} returned HTTP {resp.status if resp else '?'} — trying next")
        except Exception:
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                if resp and resp.status < 400:
                    await asyncio.sleep(delay + 3)  # extra wait for JS render
                    return True
            except Exception as e:
                print(f"  [Stasher] {url} failed: {e}")
    return False


async def _search_for_city(page: Page, city: str, delay: float) -> bool:
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(2)

    search_selectors = [
        'input[placeholder*="city" i]',
        'input[placeholder*="location" i]',
        'input[placeholder*="where" i]',
        'input[placeholder*="search" i]',
        'input[placeholder*="store" i]',
        'input[placeholder*="address" i]',
        'input[type="search"]',
        'input[name="search"]',
        'input[name="location"]',
        '[data-testid*="search"] input',
        '[class*="search"] input',
        'form input[type="text"]',
    ]
    search_input = None
    for sel in search_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1_500):
                search_input = el
                print(f"  [Stasher] Found search input via: {sel}")
                break
        except Exception:
            continue

    if search_input is None:
        print(f"  [Stasher] Could not find search input for '{city}'")
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
    for sel in suggestion_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2_000):
                await el.click()
                await asyncio.sleep(delay)
                return True
        except Exception:
            continue

    await search_input.press("Enter")
    await asyncio.sleep(delay)
    return True


def _walk_json_for_stashpoints(obj: Any, results: list[dict]) -> None:
    """Recursively walk JSON looking for lists of objects with a 'name' and price field."""
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                # Stashpoint-like object: has a name AND some price/rate field
                keys = set(k.lower() for k in item.keys())
                has_name = any(k in keys for k in ("name", "title", "venue_name"))
                has_price = any(k in keys for k in ("price", "rate", "daily", "per_day",
                                                     "price_per_day", "daily_rate", "priceperday",
                                                     "dailyprice", "amount", "fee"))
                if has_name and has_price:
                    results.append(item)
                else:
                    _walk_json_for_stashpoints(item, results)
            else:
                _walk_json_for_stashpoints(item, results)
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk_json_for_stashpoints(v, results)


async def _extract_next_data(page: Page, city: str, scraped_at: datetime) -> list[PriceRecord]:
    """
    Extract pricing from Stasher's embedded Next.js __NEXT_DATA__ script tag.
    This is more reliable than text scanning.
    """
    try:
        raw = await page.evaluate(
            "() => { const el = document.getElementById('__NEXT_DATA__'); return el ? el.textContent : null; }"
        )
        if not raw:
            return []
        data = json.loads(raw)
        page_props = data.get("props", {}).get("pageProps", {})
        print(f"  [Stasher] __NEXT_DATA__ pageProps keys: {list(page_props.keys())[:15]}")

        # Try known locations for stashpoint lists
        candidates: list[dict] = []
        for key in ("stashpoints", "locations", "venues", "spots", "results", "data", "items"):
            val = page_props.get(key)
            if isinstance(val, list) and val and isinstance(val[0], dict):
                candidates = val
                print(f"  [Stasher] Found {len(candidates)} items under pageProps['{key}']")
                break
        if not candidates:
            _walk_json_for_stashpoints(page_props, candidates)
            if candidates:
                print(f"  [Stasher] Found {len(candidates)} stashpoint-like items via deep search")

        records = _parse_api_locations(candidates, city, scraped_at)
        if records:
            print(f"  [Stasher] Parsed {len(records)} price records from __NEXT_DATA__")
        else:
            print(f"  [Stasher] __NEXT_DATA__ found but no recognisable prices")
        return records

    except Exception as e:
        print(f"  [Stasher] __NEXT_DATA__ extraction failed: {e}")
        return []


async def _body_text_scan(page: Page, city: str, scraped_at: datetime) -> list[PriceRecord]:
    """Scan full page body for price patterns — last resort."""
    records: list[PriceRecord] = []
    all_text = ""
    # Try inner_text first (visible text only), then JS eval (includes hidden elements)
    for getter in [
        lambda: page.inner_text("body"),
        lambda: page.evaluate("document.body.innerText"),
        lambda: page.locator("body").text_content(),
    ]:
        try:
            all_text = await getter()
            if all_text and all_text.strip():
                break
        except Exception:
            continue
    if not all_text or not all_text.strip():
        print(f"  [Stasher] Page body completely empty after all attempts")
        return records
    snippet = all_text.replace("\n", " ").strip()
    print(f"  [Stasher] Page snippet: {snippet[:500]}")
    # Try strict pattern first: £X.XX/day or £X.XX per day
    price_re_strict = re.compile(
        r"([£$€])\s*([\d.]+)\s*(?:/|per|a)\s*(?:bag\s*(?:/|per)\s*)?day",
        re.IGNORECASE,
    )
    # Loose fallback: "from £X.XX" without unit
    price_re_loose = re.compile(r"from\s+([£$€])\s*([\d.]+)\b", re.IGNORECASE)

    seen: set[float] = set()
    for m in price_re_strict.finditer(all_text):
        symbol, amount_str = m.group(1), m.group(2)
        price = float(amount_str)
        if price > 0 and price not in seen:
            seen.add(price)
            currency = CURRENCY_MAP.get(symbol, "GBP")
            records.append(PriceRecord(
                company="Stasher", city=city,
                location_name="Stasher location", address="",
                size="flat-rate", price=price, currency=currency,
                price_unit="day", scraped_at=scraped_at,
            ))
    if not records:
        for m in price_re_loose.finditer(all_text):
            symbol, amount_str = m.group(1), m.group(2)
            price = float(amount_str)
            if 1.0 <= price <= 100.0 and price not in seen:
                seen.add(price)
                currency = CURRENCY_MAP.get(symbol, "GBP")
                records.append(PriceRecord(
                    company="Stasher", city=city,
                    location_name="Stasher location", address="",
                    size="flat-rate", price=price, currency=currency,
                    price_unit="day", scraped_at=scraped_at,
                ))
    if records:
        print(f"  [Stasher] Body scan found {len(records)} distinct price(s)")
    else:
        print(f"  [Stasher] No prices found anywhere on the page")
    return records


async def _dom_scrape_city(page: Page, city: str, delay: float, max_locs: int) -> list[PriceRecord]:
    records: list[PriceRecord] = []
    scraped_at = datetime.now(timezone.utc)

    navigated = await _navigate_to_city(page, city, delay)
    if not navigated:
        ok = await _search_for_city(page, city, delay)
        if not ok:
            return records

    location_selectors = [
        '[data-testid*="stashpoint"]',
        '[data-testid*="location"]',
        '[data-testid*="venue"]',
        '[class*="stashpoint"]',
        '[class*="StashPoint"]',
        '[class*="location-card"]',
        '[class*="LocationCard"]',
        '[class*="store-card"]',
        '[class*="StoreCard"]',
        '[class*="venue-card"]',
        '[class*="VenueCard"]',
        '[class*="result-item"]',
        '[class*="ResultItem"]',
        '[class*="storage-location"]',
        '[class*="listing-item"]',
        '[class*="ListingItem"]',
        'li[class*="listing"]',
        'li[class*="location"]',
        'article[class*="location"]',
        'article[class*="store"]',
        'a[href*="/luggage-storage/"]',
        'a[href*="/stash/"]',
        'a[href*="/stashpoint"]',
    ]
    location_elements = []
    for sel in location_selectors:
        els = page.locator(sel)
        count = await els.count()
        if count > 0:
            location_elements = [els.nth(i) for i in range(count)]
            print(f"  [Stasher] Found {count} locations using selector '{sel}'")
            break

    if not location_elements:
        print(f"  [Stasher] No location cards found — trying __NEXT_DATA__ …")
        records = await _extract_next_data(page, city, scraped_at)
        if records:
            return records
        print(f"  [Stasher] Falling back to body text scan …")
        return await _body_text_scan(page, city, scraped_at)

    if max_locs and len(location_elements) > max_locs:
        location_elements = location_elements[:max_locs]

    for i, loc_el in enumerate(location_elements):
        try:
            # Extract name
            name = ""
            for sel in ('[class*="name"]', '[class*="title"]', 'h2', 'h3', 'strong'):
                try:
                    t = await loc_el.locator(sel).first.inner_text(timeout=1_000)
                    if t.strip():
                        name = t.strip()
                        break
                except Exception:
                    continue

            # Extract address
            address = ""
            for sel in ('[class*="address"]', '[class*="subtitle"]', 'p', 'span'):
                try:
                    t = await loc_el.locator(sel).first.inner_text(timeout=1_000)
                    if t.strip():
                        address = t.strip()
                        break
                except Exception:
                    continue

            # Extract price from card text
            card_text = await loc_el.inner_text(timeout=2_000)
            price, currency, unit = _parse_price_text(card_text)

            if price > 0:
                records.append(PriceRecord(
                    company="Stasher",
                    city=city,
                    location_name=name or f"Stasher location {i+1}",
                    address=address,
                    size="flat-rate",
                    price=price,
                    currency=currency or "GBP",
                    price_unit=unit or "day",
                    scraped_at=scraped_at,
                ))
                print(f"  [Stasher] [{i+1}] {name}: {currency}{price}/{unit}")
            else:
                # Try parent element text as well (in case price is outside the anchor)
                try:
                    parent_text = await loc_el.locator("xpath=..").inner_text(timeout=1_000)
                    price, currency, unit = _parse_price_text(parent_text)
                    if price > 0:
                        records.append(PriceRecord(
                            company="Stasher",
                            city=city,
                            location_name=name or f"Stasher location {i+1}",
                            address=address,
                            size="flat-rate",
                            price=price,
                            currency=currency or "GBP",
                            price_unit=unit or "day",
                            scraped_at=scraped_at,
                        ))
                        print(f"  [Stasher] [{i+1}] {name}: {currency}{price}/{unit} (from parent)")
                    else:
                        print(f"  [Stasher] [{i+1}] {name}: no price found in card text")
                except Exception:
                    print(f"  [Stasher] [{i+1}] {name}: no price found in card text")

        except Exception as e:
            print(f"  [Stasher] Error on location #{i+1}: {e}")
            continue

    # If card iteration found nothing, fall back to full body scan
    if not records:
        print(f"  [Stasher] No prices in cards — scanning full page body …")
        return await _body_text_scan(page, city, scraped_at)

    return records


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def _trigger_search(page: Page, city: str, delay: float) -> bool:
    """
    After navigating to a Stasher city page, trigger a search so the
    stashpoint API call fires. Returns True if something was triggered.
    """
    await asyncio.sleep(1)

    # Log all visible buttons for diagnosis
    try:
        buttons = await page.evaluate("""
            () => Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"]'))
                .filter(el => el.offsetParent !== null)
                .map(el => ({ tag: el.tagName, type: el.type||'', text: el.innerText.trim().slice(0,40), cls: el.className.slice(0,60) }))
        """)
        if buttons:
            print(f"  [Stasher] Visible buttons on page: {buttons[:8]}")
        else:
            print(f"  [Stasher] No visible buttons found on page")
    except Exception:
        pass

    # Try explicit selectors first
    submit_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button[class*="search" i]',
        'button[class*="Search"]',
        '[data-testid*="search"] button',
        '[data-testid*="submit"]',
        'form button',
        '[role="button"][class*="search" i]',
        'button[class*="btn"]',
        'button',  # last resort: first button on page
    ]
    for sel in submit_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1_500):
                text = (await btn.inner_text(timeout=500)).strip()
                await btn.click()
                print(f"  [Stasher] Clicked button ({sel!r} text={text!r})")
                await asyncio.sleep(delay + 2)
                return True
        except Exception:
            continue

    # Fallback: press Enter in any search input
    for inp_sel in ('input[type="search"]', 'input[type="text"]', 'input'):
        try:
            inp = page.locator(inp_sel).first
            if await inp.is_visible(timeout=1_500):
                await inp.press("Enter")
                print(f"  [Stasher] Pressed Enter in input ({inp_sel})")
                await asyncio.sleep(delay + 2)
                return True
        except Exception:
            continue

    print(f"  [Stasher] Could not find any interactive element to trigger search")
    return False


async def _fetch_api_direct(city: str, scraped_at: datetime, max_locs: int) -> list[PriceRecord]:
    """
    Try Stasher's backend REST API directly — no browser needed.
    Attempts several plausible endpoint patterns.
    """
    slug = _city_to_slug(city)
    lat, lng = CITY_COORDS.get(city.lower(), (51.5074, -0.1278))

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": f"https://stasher.com/luggage-storage/{slug}",
        "Origin": "https://stasher.com",
    }

    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    # Try several plausible endpoint + param combinations, with and without date
    candidates = [
        ("https://stasher.com/api/stashpoints", {"city": slug, "date": tomorrow, "bags": 1}),
        ("https://stasher.com/api/stashpoints", {"lat": lat, "lng": lng, "radius": 5000, "date": tomorrow}),
        ("https://stasher.com/api/v1/stashpoints", {"city": slug, "date": tomorrow}),
        ("https://stasher.com/api/v2/stashpoints", {"city": slug, "date": tomorrow}),
        ("https://stasher.com/api/v3/stashpoints", {"city": slug, "date": tomorrow}),
        ("https://stasher.com/api/search", {"q": city, "date": tomorrow, "type": "stashpoints"}),
        ("https://stasher.com/api/locations", {"city": slug, "date": tomorrow}),
        # Without date as fallback
        ("https://stasher.com/api/stashpoints", {"city": slug}),
        ("https://stasher.com/api/v3/stashpoints", {"city": slug}),
    ]

    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        for url, params in candidates:
            try:
                resp = await client.get(url, params=params)
                print(f"  [Stasher] Direct API {url}: HTTP {resp.status_code}")
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except Exception:
                        continue
                    # Extract location list from various response shapes
                    locations: list[dict] = []
                    if isinstance(data, list) and data and isinstance(data[0], dict):
                        locations = data
                    elif isinstance(data, dict):
                        for key in ("stashpoints", "locations", "results", "data", "items", "venues"):
                            val = data.get(key)
                            if isinstance(val, list) and val:
                                locations = val
                                break
                    if locations:
                        print(f"  [Stasher] Direct API returned {len(locations)} location(s)")
                        print(f"  [Stasher] First item keys: {list(locations[0].keys())[:20]}")
                        records = _parse_api_locations(locations, city, scraped_at)
                        if max_locs:
                            records = records[:max_locs]
                        return records
            except Exception as e:
                print(f"  [Stasher] Direct API error ({url}): {e}")
                continue

    return []


async def scrape_city(
    page: Page,
    city: str,
    delay: float = 2.0,
    max_locations: int = 0,
) -> list[PriceRecord]:
    """
    Scrape pricing for all Stasher locations in *city*.

    Strategy:
    1. Try direct API call (httpx) — fastest, no browser needed
    2. Navigate to city page with date param → intercept stashpoint API calls
    3. Try homepage search to trigger API
    4. Fall back to __NEXT_DATA__ + body text scan
    """
    scraped_at = datetime.now(timezone.utc)

    # Strategy 1: direct API (fast, no browser)
    print(f"[{city}] [Stasher] Trying direct API …")
    records = await _fetch_api_direct(city, scraped_at, max_locations)
    if records:
        print(f"[{city}] [Stasher] Direct API succeeded — {len(records)} record(s)")
        return records

    # Strategy 2: navigate directly to city page with date — intercept the stashpoint API call
    collector = _ApiCollector()
    page.on("response", lambda r: asyncio.ensure_future(collector.handle_response(r)))

    slug = _city_to_slug(city)
    country = _get_country(city)
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"[{city}] [Stasher] Navigating to city page with date …")
    city_page_loaded = False
    for url in [
        CITY_PAGE_SIMPLE.format(slug=slug) + f"?date={tomorrow}&bags=1",
        CITY_PAGE_TEMPLATE.format(country=country, slug=slug) + f"?date={tomorrow}&bags=1",
        CITY_PAGE_SIMPLE.format(slug=slug),
        CITY_PAGE_TEMPLATE.format(country=country, slug=slug),
    ]:
        try:
            print(f"  [Stasher] Trying: {url}")
            resp = await page.goto(url, wait_until="networkidle", timeout=45_000)
            if resp and resp.status < 400:
                await asyncio.sleep(delay + 2)  # let JS finish
                city_page_loaded = True
                break
        except Exception:
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                if resp and resp.status < 400:
                    await asyncio.sleep(delay + 4)
                    city_page_loaded = True
                    break
            except Exception as e:
                print(f"  [Stasher] {url} failed: {e}")

    # Check if city page navigation triggered any API calls
    if collector.locations:
        print(f"[{city}] [Stasher] City page API: {len(collector.locations)} location(s)")
        first = collector.locations[0]
        print(f"  [Stasher] API keys: {list(first.keys())[:20]}")
        records = _parse_api_locations(collector.locations, city, scraped_at)
        if records:
            if max_locations:
                records = records[:max_locations]
            return records
        print(f"[{city}] [Stasher] Locations found but no recognised price fields — falling back")

    # Strategy 3: homepage search to trigger stashpoint API
    print(f"[{city}] [Stasher] Searching via homepage …")
    try:
        await page.goto(BASE_URL, wait_until="networkidle", timeout=45_000)
        await asyncio.sleep(2)

        search_filled = False
        for sel in [
            'input[placeholder*="city" i]',
            'input[placeholder*="location" i]',
            'input[placeholder*="where" i]',
            'input[placeholder*="search" i]',
            'input[placeholder*="store" i]',
            'input[type="search"]',
            'input[type="text"]',
        ]:
            try:
                inp = page.locator(sel).first
                if await inp.is_visible(timeout=1_500):
                    await inp.click()
                    await inp.fill(city)
                    await asyncio.sleep(1)
                    print(f"  [Stasher] Filled search input ({sel})")
                    search_filled = True
                    break
            except Exception:
                continue

        if not search_filled:
            try:
                inputs = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('input'))
                        .filter(e => e.offsetParent !== null)
                        .map(e => ({type: e.type, placeholder: e.placeholder, cls: e.className.slice(0,50)}))
                """)
                print(f"  [Stasher] Visible inputs: {inputs[:5]}")
            except Exception:
                pass

        if search_filled:
            suggestion_clicked = False
            for sel in ['[role="option"]', '[class*="suggestion"]', '[class*="autocomplete"] li',
                        'ul[class*="result"] li', '[class*="dropdown"] li']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2_000):
                        await el.click()
                        suggestion_clicked = True
                        print(f"  [Stasher] Clicked autocomplete suggestion ({sel})")
                        break
                except Exception:
                    continue

            if not suggestion_clicked:
                await page.keyboard.press("Enter")
                print(f"  [Stasher] Pressed Enter to submit search")

            await asyncio.sleep(delay + 3)

    except Exception as e:
        print(f"[{city}] [Stasher] Homepage search failed ({e})")

    # Check if homepage search triggered API calls
    if collector.locations:
        print(f"[{city}] [Stasher] Homepage API: {len(collector.locations)} location(s)")
        records = _parse_api_locations(collector.locations, city, scraped_at)
        if records:
            if max_locations:
                records = records[:max_locations]
            return records
        print(f"[{city}] [Stasher] Locations found but no recognised price fields — falling back")
    else:
        print(f"[{city}] [Stasher] No API data captured — trying DOM/body scan …")

    # Strategy 4: DOM scrape the city page we already loaded
    if city_page_loaded:
        return await _dom_scrape_city(page, city, delay, max_locations)

    # Navigate once more if city page never loaded
    for url in [CITY_PAGE_SIMPLE.format(slug=slug),
                CITY_PAGE_TEMPLATE.format(country=country, slug=slug)]:
        try:
            resp = await page.goto(url, wait_until="networkidle", timeout=45_000)
            if resp and resp.status < 400:
                await asyncio.sleep(delay)
                break
        except Exception:
            continue

    return await _dom_scrape_city(page, city, delay, max_locations)
