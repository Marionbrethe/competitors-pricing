"""
Radical Storage (radicalstorage.com) pricing extractor.

Strategy:
  1. Intercept XHR/fetch responses to find an internal JSON API for locations/prices.
  2. If API data is found, parse it directly (fast, reliable).
  3. Fall back to DOM scraping: navigate to city page → iterate location cards → parse prices.

URL patterns:
  https://radicalstorage.com/luggage-storage/{city-slug}
  https://radicalstorage.com/storage-list/{city-slug}  (alternative full list)

Pricing model: flat rate — all bag sizes charged the same daily rate per location.
Note: Radical's hero banner often shows a low "from" price (e.g. €1.90) for marketing;
      always use the price from individual location cards, not the banner.
"""
import asyncio
import re
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import Page, Response

from scraper.models import PriceRecord


BASE_URL = "https://radicalstorage.com"
CITY_PAGE_TEMPLATE = BASE_URL + "/luggage-storage/{slug}"
CITY_LIST_TEMPLATE = BASE_URL + "/storage-list/{slug}"

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
    r"\s*(?:/|per|a)\s*(?:bag\s*(?:/|per)\s*)?(\w+)",
    re.IGNORECASE,
)

# Secondary pattern: "from £7.90" without a time unit (used on listing/area pages)
_FROM_PRICE_RE = re.compile(
    r"from\s+([£$€¥]|A\$|C\$)\s*([\d,]+(?:\.\d+)?)\b",
    re.IGNORECASE,
)


def _city_to_slug(city: str) -> str:
    """Convert 'New York' → 'new-york', stripping accents."""
    import unicodedata
    s = city.strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace(" ", "-")


# ---------------------------------------------------------------------------
# API interception
# ---------------------------------------------------------------------------

class _ApiCollector:
    """Accumulates JSON responses that look like location/pricing data."""

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
                for key in ("data", "results", "locations", "items", "shops", "spots", "venues"):
                    if key in body and isinstance(body[key], list) and body[key]:
                        self.locations.extend(body[key])
                        break
        except Exception:
            pass


def _parse_api_locations(raw: list[dict], city: str, scraped_at: datetime) -> list[PriceRecord]:
    records: list[PriceRecord] = []
    for loc in raw:
        name = (
            loc.get("name") or loc.get("title") or loc.get("shop_name")
            or loc.get("storeName") or "Unknown"
        )
        address = (
            loc.get("address") or loc.get("full_address")
            or loc.get("formattedAddress") or ""
        )
        price_val = (
            loc.get("price") or loc.get("dailyPrice") or loc.get("price_per_day")
            or loc.get("pricePerDay") or loc.get("rate") or 0
        )
        currency = (
            loc.get("currency") or loc.get("currencyCode") or loc.get("currency_code") or "EUR"
        )
        if not price_val:
            pricing = loc.get("pricing") or loc.get("prices") or {}
            if isinstance(pricing, dict):
                price_val = (
                    pricing.get("daily") or pricing.get("perDay")
                    or pricing.get("price") or pricing.get("amount") or 0
                )

        if price_val and float(price_val) > 0:
            records.append(PriceRecord(
                company="Radical Storage",
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
    """Parse '€4.90 / bag / day' → (4.90, 'EUR', 'day'). Returns (0, '', '') on failure."""
    m = _PRICE_RE.search(text)
    if not m:
        return 0.0, "", ""
    symbol, amount_str, unit = m.group(1), m.group(2), m.group(3)
    amount = float(amount_str.replace(",", ""))
    # Ignore obviously wrong banner/marketing prices (Radical sometimes shows €1.90 as promo)
    if amount < 2.0:
        return 0.0, "", ""
    currency = CURRENCY_MAP.get(symbol, symbol)
    return amount, currency, unit.lower()


def _parse_price_loose(text: str) -> tuple[float, str]:
    """
    Looser parse for 'from £7.90' (no unit) on listing/area pages.
    Returns (price, currency). Assumes daily rate.
    Uses > 3.0 floor to skip the £1.90 promo banner.
    """
    best = 0.0
    best_currency = ""
    for m in _FROM_PRICE_RE.finditer(text):
        symbol, amount_str = m.group(1), m.group(2)
        amount = float(amount_str.replace(",", ""))
        if 3.0 <= amount <= 100.0 and amount > best:
            best = amount
            best_currency = CURRENCY_MAP.get(symbol, symbol)
    return best, best_currency


async def _try_navigate(page: Page, url: str, delay: float) -> bool:
    try:
        resp = await page.goto(url, wait_until="networkidle", timeout=45_000)
        if resp and resp.status >= 400:
            return False
        await asyncio.sleep(delay)
        return True
    except Exception:
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if resp and resp.status >= 400:
                return False
            await asyncio.sleep(delay + 2)
            return True
        except Exception:
            return False


async def _search_for_city(page: Page, city: str, delay: float) -> bool:
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(2)

    search_selectors = [
        'input[placeholder*="city" i]',
        'input[placeholder*="location" i]',
        'input[placeholder*="where" i]',
        'input[placeholder*="search" i]',
        'input[type="search"]',
        'input[name="search"]',
        '[class*="search"] input',
        'form input[type="text"]',
    ]
    search_input = None
    for sel in search_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1_500):
                search_input = el
                print(f"  [Radical] Found search input via: {sel}")
                break
        except Exception:
            continue

    if search_input is None:
        print(f"  [Radical] Could not find search input for '{city}'")
        return False

    await search_input.click()
    await search_input.fill(city)
    await asyncio.sleep(1)

    for sel in ('[role="option"]', '[class*="suggestion"]', '[class*="autocomplete"] li',
                '[class*="dropdown"] li', '[data-testid*="suggestion"]'):
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


async def _dom_scrape_city(page: Page, city: str, delay: float, max_locs: int) -> list[PriceRecord]:
    records: list[PriceRecord] = []
    scraped_at = datetime.now(timezone.utc)
    slug = _city_to_slug(city)

    # Try both URL patterns; Radical has two listing pages
    navigated = await _try_navigate(page, CITY_PAGE_TEMPLATE.format(slug=slug), delay)
    if not navigated:
        navigated = await _try_navigate(page, CITY_LIST_TEMPLATE.format(slug=slug), delay)
    if not navigated:
        ok = await _search_for_city(page, city, delay)
        if not ok:
            return records

    # Phase 1 — try dedicated card selectors (not anchor links)
    card_selectors = [
        '[data-testid*="location"]',
        '[data-testid*="shop"]',
        '[class*="location-card"]',
        '[class*="LocationCard"]',
        '[class*="shop-card"]',
        '[class*="ShopCard"]',
        '[class*="store-card"]',
        '[class*="storage-item"]',
        '[class*="result-item"]',
        'li[class*="location"]',
        'article[class*="location"]',
    ]
    location_elements = []
    for sel in card_selectors:
        els = page.locator(sel)
        count = await els.count()
        if count > 0:
            location_elements = [els.nth(i) for i in range(count)]
            print(f"  [Radical] Found {count} cards using selector '{sel}'")
            break

    if location_elements:
        if max_locs and len(location_elements) > max_locs:
            location_elements = location_elements[:max_locs]

        for i, loc_el in enumerate(location_elements):
            try:
                name = ""
                for sel in ('[class*="name"]', '[class*="title"]', 'h2', 'h3', 'h4', 'strong'):
                    try:
                        t = await loc_el.locator(sel).first.inner_text(timeout=1_000)
                        if t.strip():
                            name = t.strip()
                            break
                    except Exception:
                        continue

                address = ""
                for sel in ('[class*="address"]', '[class*="subtitle"]', '[class*="location"]', 'p', 'span'):
                    try:
                        t = await loc_el.locator(sel).first.inner_text(timeout=1_000)
                        if t.strip():
                            address = t.strip()
                            break
                    except Exception:
                        continue

                card_text = await loc_el.inner_text(timeout=2_000)
                price, currency, unit = _parse_price_text(card_text)

                if price <= 0:
                    try:
                        parent_text = await loc_el.locator("xpath=..").inner_text(timeout=1_000)
                        price, currency, unit = _parse_price_text(parent_text)
                    except Exception:
                        pass

                if price > 0:
                    records.append(PriceRecord(
                        company="Radical Storage",
                        city=city,
                        location_name=name or f"Radical location {i+1}",
                        address=address,
                        size="flat-rate",
                        price=price,
                        currency=currency or "EUR",
                        price_unit=unit or "day",
                        scraped_at=scraped_at,
                    ))
                    print(f"  [Radical] [{i+1}] {name}: {currency}{price}/{unit}")

            except Exception as e:
                print(f"  [Radical] Error on card #{i+1}: {e}")
                continue

        if records:
            return records
        print(f"  [Radical] Cards found but no prices — falling through to link navigation")

    # Phase 2 — collect href links and visit individual location pages
    link_els = page.locator('a[href*="/luggage-storage/"]')
    link_count = await link_els.count()
    if link_count > 0:
        # Filter to links that look like individual location pages (not city-level links)
        hrefs: list[str] = []
        for i in range(link_count):
            try:
                href = await link_els.nth(i).get_attribute("href", timeout=500)
                if href:
                    # Individual location pages have ≥3 path segments
                    parts = [p for p in href.split("/") if p]
                    if len(parts) >= 3:
                        full = href if href.startswith("http") else BASE_URL + href
                        if full not in hrefs:
                            hrefs.append(full)
            except Exception:
                continue

        sample = hrefs[: max_locs if max_locs else 20]
        print(f"  [Radical] Visiting {len(sample)} individual location pages for prices …")

        for i, loc_url in enumerate(sample):
            try:
                resp = await page.goto(loc_url, wait_until="networkidle", timeout=45_000)
                if resp and resp.status >= 400:
                    continue
                await asyncio.sleep(1)

                loc_name = ""
                for sel in ('h1', 'h2', '[class*="name"]', '[class*="title"]'):
                    try:
                        t = await page.locator(sel).first.inner_text(timeout=1_000)
                        if t.strip():
                            loc_name = t.strip()
                            break
                    except Exception:
                        continue

                page_text = await page.inner_text("body")
                price, currency, unit = _parse_price_text(page_text)
                if price <= 0:
                    # Fallback: "from £X.XX" without unit (common on Radical area pages)
                    price, currency = _parse_price_loose(page_text)
                    unit = "day"
                if price > 0:
                    records.append(PriceRecord(
                        company="Radical Storage",
                        city=city,
                        location_name=loc_name or f"Radical location {i+1}",
                        address="",
                        size="flat-rate",
                        price=price,
                        currency=currency or "EUR",
                        price_unit=unit,
                        scraped_at=scraped_at,
                    ))
                    print(f"  [Radical] [{i+1}] {loc_name}: {currency}{price}/day")
                else:
                    snippet = page_text.replace("\n", " ").strip()
                    if i == 0:
                        print(f"  [Radical] [{i+1}] no price — page snippet: {snippet[:400]}")
                    else:
                        print(f"  [Radical] [{i+1}] {loc_name}: no price on individual page")

            except Exception as e:
                print(f"  [Radical] Error visiting location #{i+1}: {e}")
                continue

        if records:
            return records

    # Phase 3 — full body scan on the listing page
    # (re-navigate since we may have left the city page during phase 2)
    print(f"  [Radical] Falling back to body-text scan on city listing page …")
    try:
        await page.goto(CITY_PAGE_TEMPLATE.format(slug=slug), wait_until="networkidle", timeout=45_000)
        await asyncio.sleep(2)
    except Exception:
        pass

    try:
        all_text = await page.inner_text("body")
    except Exception:
        return records

    snippet = all_text.replace("\n", " ").strip()
    print(f"  [Radical] Page snippet: {snippet[:500]}")

    price_re = re.compile(
        r"([£$€])\s*([\d.]+)\s*(?:/|per|a)\s*(?:bag\s*(?:/|per)\s*)?day",
        re.IGNORECASE,
    )
    seen: set[float] = set()
    for m in price_re.finditer(all_text):
        symbol, amount_str = m.group(1), m.group(2)
        price = float(amount_str)
        if price >= 2.0 and price not in seen:
            seen.add(price)
            currency = CURRENCY_MAP.get(symbol, "EUR")
            records.append(PriceRecord(
                company="Radical Storage",
                city=city,
                location_name="Radical Storage location",
                address="",
                size="flat-rate",
                price=price,
                currency=currency,
                price_unit="day",
                scraped_at=scraped_at,
            ))
    if records:
        print(f"  [Radical] Body scan found {len(records)} distinct price(s)")
    else:
        print(f"  [Radical] No prices found anywhere on the page")

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
    Scrape pricing for all Radical Storage locations in *city*.
    API interception first; falls back to DOM scraping.
    """
    scraped_at = datetime.now(timezone.utc)
    collector = _ApiCollector()
    page.on("response", lambda r: asyncio.ensure_future(collector.handle_response(r)))

    slug = _city_to_slug(city)
    city_url = CITY_PAGE_TEMPLATE.format(slug=slug)
    print(f"[{city}] [Radical] Navigating to {city_url} …")

    try:
        resp = await page.goto(city_url, wait_until="networkidle", timeout=45_000)
        if resp and resp.status >= 400:
            print(f"[{city}] [Radical] Direct URL returned HTTP {resp.status} — trying list URL")
            list_url = CITY_LIST_TEMPLATE.format(slug=slug)
            resp2 = await page.goto(list_url, wait_until="networkidle", timeout=45_000)
            if resp2 and resp2.status >= 400:
                print(f"[{city}] [Radical] List URL also failed — trying homepage search")
                await _search_for_city(page, city, delay)
    except Exception as e:
        print(f"[{city}] [Radical] Direct URL failed ({e}) — trying homepage search")
        await _search_for_city(page, city, delay)

    await asyncio.sleep(delay)
    await asyncio.sleep(2)

    if collector.locations:
        print(f"[{city}] [Radical] API interception succeeded — {len(collector.locations)} location(s) found")
        # Debug: show keys of first item so we can map price fields
        first = collector.locations[0]
        print(f"  [Radical] API first-item keys: {list(first.keys())[:20]}")
        records = _parse_api_locations(collector.locations, city, scraped_at)
        if records:
            if max_locations:
                records = records[:max_locations]
            return records
        print(f"[{city}] [Radical] API data found but price fields not recognised — falling back to DOM")

    print(f"[{city}] [Radical] Falling back to DOM scraping …")
    return await _dom_scrape_city(page, city, delay, max_locations)
