
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

logger = logging.getLogger(__name__)


class BrowserManager:
    """Attach to the user's existing, logged-in Microsoft Edge session."""

    def __init__(
        self,
        profile_dir: str | Path | None = None,
        *,
        headless: bool = False,
    ) -> None:
        local_app_data = os.environ.get("LOCALAPPDATA")

        default_dir = (
            Path(local_app_data) / "Microsoft" / "Edge" / "User Data"
            if local_app_data
            else Path.home()
            / "AppData"
            / "Local"
            / "Microsoft"
            / "Edge"
            / "User Data"
        )

        configured_dir = (
            profile_dir
            or os.environ.get("HELLO_DODO_EDGE_USER_DATA")
            or default_dir
        )

        self.profile_dir = Path(configured_dir).resolve()
        self.headless = headless

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lock = asyncio.Lock()
        self._closing = False

    @property
    def is_running(self) -> bool:
        return (
            self._context is not None
            and not self._context.is_closed()
        )

    @property
    def context(self) -> BrowserContext | None:
        return self._context if self.is_running else None

    async def start(self) -> BrowserContext:
        async with self._lock:
            if self._closing:
                raise RuntimeError("BrowserManager is closing.")

            if self.is_running:
                return self._context  # type: ignore[return-value]

            if self._playwright is not None:
                await self._stop_unlocked()

            active_port_file = self.profile_dir / "DevToolsActivePort"

            if not active_port_file.is_file():
                raise RuntimeError(
                    "Cannot connect to your existing Edge session. "
                    "Open edge://inspect, select Remote debugging, "
                    "and enable 'Allow remote debugging for this "
                    "browser instance'. Check that DevToolsActivePort "
                    f"exists here: {active_port_file}"
                )

            try:
                lines = active_port_file.read_text(
                    encoding="utf-8"
                ).splitlines()

                port = int(lines[0].strip())

                if not 1 <= port <= 65535:
                    raise ValueError("Invalid debugging port.")

            except (OSError, ValueError, IndexError) as exc:
                raise RuntimeError(
                    "Edge remote-debugging information is missing "
                    "or invalid. Re-enable remote debugging in "
                    "edge://inspect and try again."
                ) from exc

            playwright = await async_playwright().start()

            try:
                browser = await playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}",
                    timeout=10000,
                )

                if not browser.contexts:
                    raise RuntimeError(
                        "Connected to Edge, but no browser context "
                        "is available."
                    )

                context = browser.contexts[0]

            except Exception as exc:
                await playwright.stop()
                raise RuntimeError(
                    "Could not attach to the existing Edge session. "
                    "Check edge://inspect, then retry. "
                    f"Details: {exc}"
                ) from exc

            self._playwright = playwright
            self._browser = browser
            self._context = context

            logger.info(
                "HELLO_DODO_EDGE_ATTACHED port=%s profile=%s pages=%s",
                port,
                self.profile_dir,
                len(context.pages),
            )

            return context

    async def get_context(self) -> BrowserContext:
        return await self.start()

    async def get_page(self) -> Page:
        context = await self.get_context()

        for page in context.pages:
            if not page.is_closed():
                return page

        return await context.new_page()

    async def new_page(self) -> Page:
        context = await self.get_context()
        return await context.new_page()

    async def open_url(self, url: str) -> Page:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("A non-empty URL is required.")

        page = await self.new_page()
        await page.goto(
            url.strip(),
            wait_until="domcontentloaded",
        )
        return page

    async def close(self) -> None:
        async with self._lock:
            self._closing = True
            try:
                await self._stop_unlocked()
            finally:
                self._closing = False

    async def _stop_unlocked(self) -> None:
        # Disconnect Playwright without closing the user's Edge window.
        playwright = self._playwright

        self._context = None
        self._browser = None
        self._playwright = None

        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                logger.exception(
                    "Error disconnecting from the Edge session."
                )

    async def __aenter__(self) -> BrowserManager:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        await self.close()


browser_manager = BrowserManager()