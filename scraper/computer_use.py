"""
Anthropic Computer Use API scraping mode.

Uses Claude's visual intelligence to operate a Playwright browser like a human:
screenshots feed Claude's vision; Claude emits click/type/scroll actions; the
agent loop terminates when Claude calls the custom `report_pricing_data` tool.

Requires ANTHROPIC_API_KEY in the environment (or a .env file at the project root).

Usage:
    python main.py --city "London" --mode computer-use
"""
import base64
import os
from datetime import datetime, timezone
from typing import Any

import anthropic
from playwright.async_api import Page

from scraper.models import PriceRecord

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL = "claude-sonnet-4-6"
TOOL_VERSION = "computer_20251124"
BETA_HEADER = "computer-use-2025-11-24"
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800
MAX_ITERATIONS = 50
REPORT_TOOL_NAME = "report_pricing_data"

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

def _build_task_prompt(city: str, max_locations: int) -> str:
    limit_clause = (
        f"Collect up to {max_locations} locations."
        if max_locations > 0
        else "Collect all available locations on the page."
    )
    return f"""You are scraping the Bounce luggage storage website (https://www.usebounce.com) for pricing data in {city}.

Your task:
1. The browser is already open. Take a screenshot to see the current page state.
2. Navigate to https://www.usebounce.com if you are not already there.
3. Find the search bar (usually has placeholder text like "Where are you going?" or "Search location").
4. Type "{city}" into the search bar and select the first autocomplete suggestion.
5. Wait for the search results to load — you will see a list of storage location cards.
6. For each location card visible on screen:
   a. Click on the location card to open its detail panel or page.
   b. Find the bag size pricing section (it shows sizes like Small, Regular, Odd-sized or Oversized, each with a price per day such as "£15.00 / day").
   c. Record the location name, address, each bag size, and its price.
   d. Navigate back to the results list (use the back button or close button).
   e. Scroll down if needed to find more locations.
7. {limit_clause}
8. When you have collected all pricing data, call the `{REPORT_TOOL_NAME}` tool with all records.

Important notes:
- Prices are shown as e.g. "£15.00 / day" — extract the numeric value, currency symbol (convert to ISO: £→GBP, $→USD, €→EUR), and unit.
- If a location shows no pricing panel, skip it.
- Do not fill in booking forms or click "Book now" buttons.
- If you encounter a cookie consent banner, dismiss it first by clicking the accept/close button.
- Call `{REPORT_TOOL_NAME}` even if you found zero locations.
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
# Agent loop
# ---------------------------------------------------------------------------

async def _run_agent_loop(
    client: anthropic.Anthropic,
    page: Page,
    city: str,
    max_locations: int,
) -> list[PriceRecord]:
    """
    Run the Computer Use agent loop for one city.

    Terminates when Claude calls `report_pricing_data` (success),
    MAX_ITERATIONS is reached, or an unrecoverable API error occurs.
    """
    messages: list[dict] = [
        {"role": "user", "content": _build_task_prompt(city, max_locations)}
    ]

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"  [CU] Iteration {iteration}/{MAX_ITERATIONS}")

        try:
            response = client.beta.messages.create(
                model=MODEL,
                max_tokens=4096,
                tools=[COMPUTER_TOOL, REPORT_TOOL],
                messages=messages,
                betas=[BETA_HEADER],
            )
        except anthropic.APIStatusError as exc:
            print(f"  [CU] API error (HTTP {exc.status_code}): {exc.message}")
            raise
        except anthropic.APIConnectionError as exc:
            print(f"  [CU] Connection error: {exc}")
            raise

        # Append Claude's response to conversation history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print(f"  [CU] Claude ended turn without calling {REPORT_TOOL_NAME}. Returning empty.")
            return []

        if response.stop_reason != "tool_use":
            print(f"  [CU] Unexpected stop_reason: {response.stop_reason!r}. Returning empty.")
            return []

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
                return _parse_report_tool_call(tool_input, city)

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

    print(f"  [CU] Reached MAX_ITERATIONS ({MAX_ITERATIONS}) without completion. Returning empty.")
    return []


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

    print(f"[{city}] Using Computer Use API (model: {MODEL})")
    try:
        records = await _run_agent_loop(client, page, city, max_locations)
    except Exception as exc:
        print(f"  [CU] Agent loop failed for {city}: {exc}")
        return []

    print(f"  [CU] Collected {len(records)} record(s) for {city}")
    return records
