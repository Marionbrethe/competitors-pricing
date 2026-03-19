"""
Anthropic Computer Use API scraping mode.

Uses Claude's visual intelligence to operate a Playwright browser like a human:
screenshots feed Claude's vision; Claude emits click/type/scroll actions; the
agent loop terminates when Claude calls the custom `report_pricing_data` tool.

Requires ANTHROPIC_API_KEY in the environment (or a .env file at the project root).

Usage:
    python main.py --city "London" --mode computer-use
"""
import asyncio
import base64
import os
import time
from datetime import datetime, timezone
from typing import Any

import anthropic
from playwright.async_api import Page

from scraper.models import PriceRecord
from scraper.export import to_csv

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL = "claude-sonnet-4-6"
TOOL_VERSION = "computer_20251124"
BETA_HEADER = "computer-use-2025-11-24"
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800
MAX_ITERATIONS = 25
REPORT_TOOL_NAME = "report_pricing_data"
# Keep this many recent user turns with full screenshots; older turns get images stripped
SCREENSHOT_HISTORY_TURNS = 3
# Retry delays (seconds) on 429 rate-limit errors
RATE_LIMIT_RETRY_DELAYS = [60, 120, 240]

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

COMPUTER_TOOL: dict[str, Any] = {
    "type": TOOL_VERSION,
    "name": "computer",
    "display_width_px": VIEWPORT_WIDTH,
    "display_height_px": VIEWPORT_HEIGHT,
    "display_number": 1,
}

REPORT_TOOL: dict[str, Any] = {
    "type": "custom",
    "name": REPORT_TOOL_NAME,
    "description": (
        "Call this tool exactly once when you have finished collecting all available "
        "pricing data for the city. Pass all records you found as a JSON array."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "locations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "location_name": {"type": "string"},
                        "address":       {"type": "string"},
                        "size":          {
                            "type": "string",
                            "description": "small | regular | odd-sized | oversized",
                        },
                        "price":         {"type": "number"},
                        "currency":      {
                            "type": "string",
                            "description": "ISO 4217 code e.g. GBP, USD, EUR",
                        },
                        "price_unit":    {
                            "type": "string",
                            "description": "e.g. day",
                        },
                    },
                    "required": ["location_name", "price", "size", "currency", "price_unit"],
                },
            }
        },
        "required": ["locations"],
    },
}


# ---------------------------------------------------------------------------
# Task prompt
# ---------------------------------------------------------------------------

def _city_slug(city: str) -> str:
    return city.strip().lower().replace(" ", "-")


def _build_task_prompt(city: str, max_locations: int, city_url: str) -> str:
    limit_clause = (
        f"Collect up to {max_locations} locations."
        if max_locations > 0
        else "Collect all available locations on the page."
    )
    return f"""The browser is already open and has been navigated to the Bounce luggage storage page for {city}:
  {city_url}

Take a screenshot first to see what is currently on screen.

Your task — extract pricing from each storage location card on this page:

1. If you see a cookie consent banner, dismiss it by clicking the accept/close button.
2. You should see a list of storage location cards on the left side of the page.
   - Each card shows a location name and address.
3. For each card:
   a. Click the card to open its detail panel (appears on the right, or as a new page).
   b. Find the bag size section — it lists sizes like Small, Regular, Odd-sized, or Oversized, each with a price like "£15.00 / day".
   c. Record the location name, address, every bag size, and its price.
   d. Go back to the location list (click the back arrow or close the panel).
   e. Scroll down to reveal more cards if needed.
4. {limit_clause}
5. When finished, call the `{REPORT_TOOL_NAME}` tool with all collected records.

Rules:
- Prices look like "£15.00 / day" — extract the number, convert the currency symbol (£→GBP, $→USD, €→EUR), and the unit (day).
- Skip any location that has no pricing shown.
- Do NOT click "Book now" or fill in any booking form.
- Call `{REPORT_TOOL_NAME}` even if you found zero locations.
- Do not navigate away from this site.
"""


# ---------------------------------------------------------------------------
# Playwright action executor
# ---------------------------------------------------------------------------

async def _execute_computer_action(
    page: Page,
    action: str,
    params: dict[str, Any],
) -> bytes | None:
    """
    Execute a single computer tool action via Playwright.
    Returns screenshot bytes if action == 'screenshot', else None.
    """
    if action == "screenshot":
        return await page.screenshot(type="png", full_page=False)

    elif action == "left_click":
        await page.mouse.click(params["coordinate"][0], params["coordinate"][1])

    elif action == "double_click":
        await page.mouse.dblclick(params["coordinate"][0], params["coordinate"][1])

    elif action == "right_click":
        await page.mouse.click(
            params["coordinate"][0], params["coordinate"][1], button="right"
        )

    elif action == "mouse_move":
        await page.mouse.move(params["coordinate"][0], params["coordinate"][1])

    elif action == "type":
        # delay=50ms between keystrokes so autocomplete JS debounce fires correctly
        await page.keyboard.type(params["text"], delay=50)

    elif action == "key":
        await page.keyboard.press(params["key"])

    elif action == "scroll":
        x, y = params["coordinate"]
        direction = params.get("direction", "down")
        amount = params.get("amount", 3)
        delta_y = amount * 100 if direction == "down" else (
            -amount * 100 if direction == "up" else 0
        )
        delta_x = amount * 100 if direction == "right" else (
            -amount * 100 if direction == "left" else 0
        )
        await page.mouse.wheel(delta_x, delta_y)

    elif action == "left_click_drag":
        start = params["start_coordinate"]
        end = params["coordinate"]
        await page.mouse.move(start[0], start[1])
        await page.mouse.down()
        await page.mouse.move(end[0], end[1])
        await page.mouse.up()

    else:
        print(f"  [CU] Unhandled action: {action!r}")

    return None


# ---------------------------------------------------------------------------
# Result parser
# ---------------------------------------------------------------------------

def _parse_report_tool_call(
    tool_input: dict[str, Any],
    city: str,
) -> list[PriceRecord]:
    scraped_at = datetime.now(timezone.utc)
    records: list[PriceRecord] = []

    locations = tool_input.get("locations", [])
    if not isinstance(locations, list):
        print(f"  [CU] report_pricing_data: 'locations' is not a list, got {type(locations)}")
        return records

    for i, loc in enumerate(locations):
        if not isinstance(loc, dict):
            print(f"  [CU] Skipping malformed entry #{i}: {loc!r}")
            continue
        try:
            records.append(PriceRecord(
                city=city,
                location_name=loc.get("location_name") or "Unknown",
                address=loc.get("address") or "",
                size=loc.get("size") or "unknown",
                price=float(loc["price"]),
                currency=loc.get("currency") or "GBP",
                price_unit=loc.get("price_unit") or "day",
                scraped_at=scraped_at,
            ))
        except (KeyError, ValueError, TypeError) as exc:
            print(f"  [CU] Skipping malformed entry #{i}: {exc}")
            continue

    return records


# ---------------------------------------------------------------------------
# Conversation history helpers
# ---------------------------------------------------------------------------

def _trim_old_screenshots(messages: list[dict]) -> list[dict]:
    """
    Replace base64 image data in older tool_result messages with a text
    placeholder to keep input token count under control.

    The most recent SCREENSHOT_HISTORY_TURNS user turns (index > 0) keep their
    images; everything older is stripped.
    """
    # Indices of user messages after the initial task prompt (index 0)
    user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user" and i > 0]
    if len(user_indices) <= SCREENSHOT_HISTORY_TURNS:
        return messages  # Nothing to trim yet

    trim_set = set(user_indices[:-SCREENSHOT_HISTORY_TURNS])

    result = []
    for i, msg in enumerate(messages):
        if i not in trim_set:
            result.append(msg)
            continue

        content = msg.get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue

        new_content = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                inner = item.get("content", [])
                new_inner = [
                    {"type": "text", "text": "[screenshot removed to reduce token usage]"}
                    if isinstance(c, dict) and c.get("type") == "image"
                    else c
                    for c in inner
                ]
                new_content.append({**item, "content": new_inner})
            else:
                new_content.append(item)

        result.append({**msg, "content": new_content})

    return result


def _save_partial(records: list[PriceRecord], city: str) -> None:
    """Write whatever records we have so far to a partial CSV."""
    if not records:
        return
    path = to_csv(records, output_dir="output", city=f"{city}_partial")
    print(f"  [CU] Partial save: {len(records)} record(s) → {path}")


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

async def _run_agent_loop(
    client: anthropic.Anthropic,
    page: Page,
    city: str,
    max_locations: int,
    city_url: str,
) -> list[PriceRecord]:
    """
    Run the Computer Use agent loop for one city.

    Terminates when Claude calls `report_pricing_data` (success),
    MAX_ITERATIONS is reached, or an unrecoverable API error occurs.
    """
    messages: list[dict] = [
        {"role": "user", "content": _build_task_prompt(city, max_locations, city_url)}
    ]
    # Accumulates records reported mid-session (e.g. per-location tool calls in future)
    partial_records: list[PriceRecord] = []

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"  [CU] Iteration {iteration}/{MAX_ITERATIONS}")

        # Trim old screenshots before each API call to keep token count low
        trimmed_messages = _trim_old_screenshots(messages)

        # Call API with retry on rate-limit (429)
        response = None
        for attempt, delay in enumerate([0] + RATE_LIMIT_RETRY_DELAYS):
            if delay:
                print(f"  [CU] Rate limited — waiting {delay}s before retry {attempt}/{len(RATE_LIMIT_RETRY_DELAYS)}...")
                time.sleep(delay)
            try:
                response = client.beta.messages.create(
                    model=MODEL,
                    max_tokens=4096,
                    tools=[COMPUTER_TOOL, REPORT_TOOL],
                    messages=trimmed_messages,
                    betas=[BETA_HEADER],
                )
                break  # success
            except anthropic.RateLimitError as exc:
                print(f"  [CU] Rate limit error (attempt {attempt + 1}): {exc.message}")
                if attempt == len(RATE_LIMIT_RETRY_DELAYS):
                    _save_partial(partial_records, city)
                    raise
            except anthropic.APIStatusError as exc:
                print(f"  [CU] API error (HTTP {exc.status_code}): {exc.message}")
                _save_partial(partial_records, city)
                raise
            except anthropic.APIConnectionError as exc:
                print(f"  [CU] Connection error: {exc}")
                _save_partial(partial_records, city)
                raise

        # Append Claude's response to the FULL (untrimmed) history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print(f"  [CU] Claude ended turn without calling {REPORT_TOOL_NAME}. Returning empty.")
            _save_partial(partial_records, city)
            return partial_records

        if response.stop_reason != "tool_use":
            print(f"  [CU] Unexpected stop_reason: {response.stop_reason!r}. Returning empty.")
            _save_partial(partial_records, city)
            return partial_records

        # Process tool use blocks
        tool_results: list[dict] = []

        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input

            # Termination: Claude has collected all data
            if tool_name == REPORT_TOOL_NAME:
                print(f"  [CU] Claude called {REPORT_TOOL_NAME} — parsing results.")
                records = _parse_report_tool_call(tool_input, city)
                # Merge with anything accumulated in partial_records
                all_records = partial_records + records
                to_csv(all_records, output_dir="output", city=city)
                return all_records

            # Computer actions
            if tool_name == "computer":
                action = tool_input.get("action")
                try:
                    screenshot_bytes = await _execute_computer_action(page, action, tool_input)
                except Exception as action_exc:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "is_error": True,
                        "content": [{"type": "text", "text": f"Action failed: {action_exc}"}],
                    })
                    continue

                if screenshot_bytes is not None:
                    b64 = base64.standard_b64encode(screenshot_bytes).decode("utf-8")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [{
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        }],
                    })
                else:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [{"type": "text", "text": "Action executed successfully."}],
                    })

            else:
                print(f"  [CU] Unknown tool: {tool_name!r}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "is_error": True,
                    "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    print(f"  [CU] Reached MAX_ITERATIONS ({MAX_ITERATIONS}) without completion.")
    _save_partial(partial_records, city)
    return partial_records


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def scrape_city_computer_use(
    page: Page,
    city: str,
    max_locations: int = 0,
) -> list[PriceRecord]:
    """
    Scrape pricing for *city* using the Anthropic Computer Use API.

    Requires ANTHROPIC_API_KEY in the environment (or a .env file loaded
    before this function is called). The Page must use a 1280x800 viewport.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. "
            "Export it or add it to a .env file in the project root."
        )

    client = anthropic.Anthropic(api_key=api_key)

    # Navigate directly to the city page before starting the agent —
    # this skips the search-bar flow and saves ~10 iterations.
    city_url = f"https://www.usebounce.com/luggage-storage/{_city_slug(city)}"
    print(f"[{city}] Pre-navigating to {city_url}")
    try:
        await page.goto(city_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)  # let JS render the location cards
    except Exception as nav_exc:
        print(f"[{city}] Pre-navigation failed ({nav_exc}) — agent will navigate manually")

    print(f"[{city}] Using Computer Use API (model: {MODEL})")
    try:
        records = await _run_agent_loop(client, page, city, max_locations, city_url)
    except Exception as exc:
        print(f"  [CU] Agent loop failed for {city}: {exc}")
        return []

    print(f"  [CU] Collected {len(records)} record(s) for {city}")
    return records
