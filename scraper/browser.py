"""
Playwright browser session factory.

Uses playwright-stealth to mask automation fingerprints and reduce
the chance of being detected/blocked by Cloudflare or similar.
"""
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

    In headed mode, tries to launch the user's real installed Chrome first
    (much harder to detect as automation than Playwright's bundled Chromium).
    Falls back to bundled Chromium if Chrome is not installed.

    Usage:
        async with new_page(headless=True) as page:
            await page.goto("https://www.usebounce.com")
    """
    async with async_playwright() as pw:
        launch_args = [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ]

        browser: Browser | None = None

        # In headed mode try real Chrome/Edge first — real browser fingerprints
        # are far less likely to be flagged by anti-bot services
        if not headless:
            for channel in ("chrome", "msedge"):
                try:
                    browser = await pw.chromium.launch(
                        headless=False,
                        channel=channel,
                        args=launch_args,
                    )
                    print(f"  [browser] Using real {channel} (headed)")
                    break
                except Exception:
                    continue

        if browser is None:
            browser = await pw.chromium.launch(
                headless=headless,
                args=launch_args,
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


@asynccontextmanager
async def new_page_for_computer_use(headless: bool = True) -> AsyncGenerator[Page, None]:
    """
    Async context manager yielding a Page configured for the Computer Use API.

    The viewport is exactly 1280x800 to match the COMPUTER_TOOL declared dimensions.
    Coordinates from Claude will only be accurate if the browser and tool share the same size.
    """
    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--force-device-scale-factor=1",
            ],
        )
        context: BrowserContext = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="en-GB",
            color_scheme="light",
        )
        page: Page = await context.new_page()
        await stealth_async(page)
        try:
            yield page
        finally:
            await context.close()
            await browser.close()
