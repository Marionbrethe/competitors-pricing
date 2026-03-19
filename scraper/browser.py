"""
Playwright browser session factory.

Uses playwright-stealth to mask automation fingerprints and reduce
the chance of being detected/blocked by Cloudflare or similar.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import stealth_async


# Realistic desktop user-agent
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)


@asynccontextmanager
async def new_page(headless: bool = True) -> AsyncGenerator[Page, None]:
    """
    Async context manager that yields a stealth-configured Playwright Page.

    Usage:
        async with new_page(headless=True) as page:
            await page.goto("https://www.usebounce.com")
    """
    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context: BrowserContext = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-GB",
        )
        page: Page = await context.new_page()
        await stealth_async(page)
        try:
            yield page
        finally:
            await context.close()
            await browser.close()
