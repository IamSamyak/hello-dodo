
"""Browser control plugin for Hello Dodo's dedicated Chromium instance."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Page

from tools.browser_manager import browser_manager
from tools.contracts import ActionSpec, ToolPlugin, ToolSpec
from tools.models import ToolContext, ToolResult


def _validate_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A non-empty URL is required.")

    url = value.strip()

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("The supplied URL is invalid.") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS URLs are allowed.")

    if not hostname:
        raise ValueError("The URL must contain a hostname.")

    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing embedded credentials are not allowed.")

    if port is not None and not 1 <= port <= 65535:
        raise ValueError("The URL contains an invalid port.")

    return url


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string.")

    return value.strip()


class BrowserPlugin(ToolPlugin):
    """Control Hello Dodo's dedicated persistent Chromium browser."""

    def __init__(self) -> None:
        self._active_page: Page | None = None

    @property
    def spec(self) -> ToolSpec:
        selector_schema = {
            "type": "string",
            "minLength": 1,
        }

        selector_type_schema = {
            "type": "string",
            "enum": ["css", "text", "label", "placeholder"],
            "default": "text",
        }

        return ToolSpec(
            name="browser",
            description="Control Hello Dodo's dedicated persistent Chromium browser.",
            version="1.2.0",
            aliases=("web_browser", "chromium"),
            actions=(
                ActionSpec(
                    name="OPEN_URL",
                    description="Open a URL in a new browser tab.",
                    input_schema={
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                ),
                ActionSpec(
                    name="NAVIGATE",
                    description="Navigate the current browser tab to a URL.",
                    input_schema={
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                ),
                ActionSpec(
                    name="GET_PAGE_INFO",
                    description="Get the current page URL and title.",
                    input_schema={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                ),
                ActionSpec(
                    name="READ_PAGE_TEXT",
                    description="Read visible text from the current page.",
                    input_schema={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                ),
                ActionSpec(
                    name="CLICK_ELEMENT",
                    description="Click an element using CSS, visible text, label, or placeholder.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "selector": selector_schema,
                            "selector_type": selector_type_schema,
                        },
                        "required": ["selector"],
                        "additionalProperties": False,
                    },
                ),
                ActionSpec(
                    name="TYPE_TEXT",
                    description="Fill an input or textarea identified by CSS, label, or placeholder.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "selector": selector_schema,
                            "selector_type": {
                                **selector_type_schema,
                                "enum": ["css", "label", "placeholder"],
                            },
                            "text": {"type": "string"},
                        },
                        "required": ["selector", "text"],
                        "additionalProperties": False,
                    },
                ),
                ActionSpec(
                    name="SCROLL_PAGE",
                    description="Scroll the current webpage up or down.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "direction": {
                                "type": "string",
                                "enum": ["up", "down"],
                            },
                            "pixels": {
                                "type": "integer",
                                "minimum": 100,
                                "maximum": 2000,
                                "default": 600,
                            },
                        },
                        "required": ["direction"],
                        "additionalProperties": False,
                    },
                ),
                ActionSpec(
                    name="PRESS_KEY",
                    description="Press a supported keyboard key on the current page.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "key": {
                                "type": "string",
                                "enum": [
                                    "Enter",
                                    "Escape",
                                    "Tab",
                                    "Space",
                                    "ArrowDown",
                                    "ArrowUp",
                                    "ArrowLeft",
                                    "ArrowRight",
                                    "Home",
                                    "End",
                                    "PageDown",
                                    "PageUp",
                                    "Backspace",
                                    "Delete",
                                ],
                            },
                        },
                        "required": ["key"],
                        "additionalProperties": False,
                    },
                ),
            ),
        )

    async def _get_active_page(self) -> Page:
        if (
            self._active_page is not None
            and not self._active_page.is_closed()
        ):
            return self._active_page

        self._active_page = await browser_manager.get_page()
        return self._active_page

    @staticmethod
    def _get_locator(page: Page, payload: dict[str, Any]):
        selector = _required_string(payload, "selector")
        selector_type = payload.get("selector_type", "text")

        if selector_type == "css":
            return page.locator(selector)

        if selector_type == "text":
            return page.get_by_text(selector, exact=True)

        if selector_type == "label":
            return page.get_by_label(selector, exact=True)

        if selector_type == "placeholder":
            return page.get_by_placeholder(selector, exact=True)

        raise ValueError(
            "selector_type must be css, text, label, or placeholder."
        )

    async def execute(
        self,
        action: str,
        payload: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        action = action.strip().upper()

        try:
            self.validate_action(action, payload)

            if action == "OPEN_URL":
                url = _validate_url(payload["url"])
                page = await browser_manager.open_url(url)
                self._active_page = page

                return ToolResult(
                    success=True,
                    message="Opened URL in a new browser tab.",
                    data={"url": page.url, "title": await page.title()},
                )

            page = await self._get_active_page()

            if action == "NAVIGATE":
                url = _validate_url(payload["url"])
                await page.goto(url, wait_until="domcontentloaded")

                return ToolResult(
                    success=True,
                    message="Navigated the current browser tab.",
                    data={"url": page.url, "title": await page.title()},
                )

            if action == "GET_PAGE_INFO":
                return ToolResult(
                    success=True,
                    message="Retrieved current browser page information.",
                    data={"url": page.url, "title": await page.title()},
                )

            if action == "READ_PAGE_TEXT":
                text = await page.locator("body").inner_text(timeout=10000)

                return ToolResult(
                    success=True,
                    message="Read text from the current browser page.",
                    data={
                        "url": page.url,
                        "title": await page.title(),
                        "text": text[:12000],
                        "truncated": len(text) > 12000,
                    },
                )

            if action == "CLICK_ELEMENT":
                locator = self._get_locator(page, payload)
                await locator.first.click(timeout=10000)

                return ToolResult(
                    success=True,
                    message="Clicked the requested page element.",
                    data={"url": page.url, "title": await page.title()},
                )

            if action == "TYPE_TEXT":
                locator = self._get_locator(page, payload)
                text = payload["text"]

                if not isinstance(text, str):
                    raise ValueError("'text' must be a string.")

                await locator.first.fill(text, timeout=10000)

                return ToolResult(
                    success=True,
                    message="Filled the requested input field.",
                    data={"url": page.url, "title": await page.title()},
                )

            if action == "SCROLL_PAGE":
                direction = payload["direction"]
                pixels = payload.get("pixels", 600)

                if direction not in {"up", "down"}:
                    raise ValueError("direction must be 'up' or 'down'.")

                if type(pixels) is not int or not 100 <= pixels <= 2000:
                    raise ValueError("pixels must be an integer from 100 to 2000.")

                distance = pixels if direction == "down" else -pixels

                await page.evaluate(
                    "(distance) => window.scrollBy(0, distance)",
                    distance,
                )

                return ToolResult(
                    success=True,
                    message=f"Scrolled the page {direction}.",
                    data={
                        "url": page.url,
                        "title": await page.title(),
                        "direction": direction,
                        "pixels": pixels,
                    },
                )

            if action == "PRESS_KEY":
                key = payload["key"]
                allowed_keys = {
                    "Enter",
                    "Escape",
                    "Tab",
                    "Space",
                    "ArrowDown",
                    "ArrowUp",
                    "ArrowLeft",
                    "ArrowRight",
                    "Home",
                    "End",
                    "PageDown",
                    "PageUp",
                    "Backspace",
                    "Delete",
                }

                if key not in allowed_keys:
                    raise ValueError("Unsupported keyboard key.")

                await page.keyboard.press(key)

                return ToolResult(
                    success=True,
                    message=f"Pressed {key}.",
                    data={"url": page.url, "title": await page.title(), "key": key},
                )

            return ToolResult(
                success=False,
                message=f"Unsupported browser action: {action}",
                error_code="UNSUPPORTED_ACTION",
            )

        except Exception as exc:
            return ToolResult(
                success=False,
                message=str(exc),
                error_code="BROWSER_ACTION_FAILED",
            )


TOOL_PLUGIN = BrowserPlugin()