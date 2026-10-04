
"""YouTube Music control through Hello Dodo's Playwright Edge session."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

from playwright.async_api import Page

from tools.browser_manager import browser_manager
from tools.contracts import ActionSpec, ToolPlugin, ToolSpec
from tools.models import ToolContext, ToolResult

logger = logging.getLogger(__name__)

TOOL_NAME = "play_music"


def _schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    return result


class MusicPlugin(ToolPlugin):
    """Search and control music in a persistent Microsoft Edge session."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=TOOL_NAME,
            description=(
                "Play songs and control YouTube Music using Hello Dodo's "
                "persistent Microsoft Edge browser."
            ),
            version="2.1.0",
            aliases=("music", "youtube_music", "youtube music"),
            actions=(
                ActionSpec(
                    name="PLAY",
                    description="Search for a song and play a result.",
                    input_schema=_schema(
                        {
                            "query": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 500,
                            }
                        },
                        ["query"],
                    ),
                ),
                ActionSpec(
                    name="PAUSE",
                    description="Pause current playback.",
                    input_schema=_schema(),
                ),
                ActionSpec(
                    name="RESUME",
                    description="Resume paused playback.",
                    input_schema=_schema(),
                ),
                ActionSpec(
                    name="NEXT",
                    description="Play the next track.",
                    input_schema=_schema(),
                ),
                ActionSpec(
                    name="PREVIOUS",
                    description="Play the previous track.",
                    input_schema=_schema(),
                ),
                ActionSpec(
                    name="STOP",
                    description="Pause current playback.",
                    input_schema=_schema(),
                ),
                ActionSpec(
                    name="SET_VOLUME",
                    description="Set player volume from 0 to 100.",
                    input_schema=_schema(
                        {
                            "volume": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 100,
                            }
                        },
                        ["volume"],
                    ),
                ),
            ),
        )

    
    async def _get_page(self) -> Page:
        """Reuse the YouTube Music tab or create a separate tab."""
        context = await browser_manager.get_context()

        for page in context.pages:
            if (
                not page.is_closed()
                and "music.youtube.com" in page.url
            ):
                return page

        # Preserve the user's existing tabs.
        return await context.new_page()

    async def _ensure_music_page(self, page: Page) -> None:
        if "music.youtube.com" not in page.url:
            await page.goto(
                "https://music.youtube.com/",
                wait_until="domcontentloaded",
                timeout=30000,
            )

        await page.wait_for_timeout(1200)

        if "accounts.google.com" in page.url:
            raise RuntimeError(
                "Sign in to YouTube Music in the Hello Dodo Edge profile, "
                "then retry the command."
            )

    async def _find_search_results(self, page: Page) -> list[dict[str, Any]]:
        """Extract candidate tracks without assuming every result has a link."""
        selectors = (
            "ytmusic-responsive-list-item-renderer",
            "ytmusic-two-row-item-renderer",
        )

        for selector in selectors:
            locator = page.locator(selector)

            try:
                await locator.first.wait_for(
                    state="visible",
                    timeout=10000,
                )
            except Exception:
                continue

            results = await locator.evaluate_all(
                """elements => elements.slice(0, 20).map((row, index) => {
                    const text = el =>
                        (el?.textContent || '').trim();

                    const titleNode = row.querySelector(
                        '.title, .title-column yt-formatted-string, '
                        + 'a[title], .flex-column yt-formatted-string'
                    );

                    const title = (
                        titleNode?.getAttribute('title')
                        || text(titleNode)
                        || text(row.querySelector('a'))
                    ).trim();

                    const links = Array.from(
                        row.querySelectorAll('a[href]')
                    );

                    const watchLink = links.find(link => {
                        const href = link.getAttribute('href') || '';
                        return /(?:\\/watch\\?v=|youtu\\.be\\/)/.test(href);
                    });

                    const videoId =
                        row.getAttribute('video-id')
                        || row.getAttribute('data-video-id')
                        || watchLink?.getAttribute('href')
                            ?.match(/[?&]v=([^&]+)/)?.[1]
                        || null;

                    const subtitle = Array.from(
                        row.querySelectorAll(
                            '.secondary-flex-columns yt-formatted-string, '
                            + '.subtitle, .byline'
                        )
                    ).map(text).filter(Boolean).join(' ');

                    return {
                        index,
                        title,
                        subtitle,
                        videoId,
                        href: watchLink?.href || null,
                        tag: row.tagName
                    };
                })"""
            )

            usable = [
                item for item in results
                if item.get("title")
            ]

            if usable:
                logger.info(
                    "MUSIC_SEARCH_RESULTS selector=%s count=%s sample=%s",
                    selector,
                    len(usable),
                    [
                        {
                            "title": item.get("title"),
                            "videoId": item.get("videoId"),
                            "href": item.get("href"),
                        }
                        for item in usable[:5]
                    ],
                )
                return usable

        return []

    async def _play(self, page: Page, query: str) -> ToolResult:
        query = query.strip()
        if not query:
            raise ValueError("Please provide a song or artist name.")

        search_url = (
            "https://music.youtube.com/search?q=" + quote_plus(query)
        )

        await page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        try:
            await page.wait_for_load_state(
                "networkidle",
                timeout=4000,
            )
        except Exception:
            # YouTube Music may keep background network requests active.
            pass

        await page.wait_for_timeout(1500)

        if "accounts.google.com" in page.url:
            raise RuntimeError(
                "YouTube Music requires sign-in in the Hello Dodo Edge profile."
            )

        results = await self._find_search_results(page)

        if not results:
            body_text = ""
            try:
                body_text = (await page.locator("body").inner_text())[:1200]
            except Exception:
                pass

            logger.error(
                "MUSIC_NO_SEARCH_RESULTS url=%s title=%s body=%r",
                page.url,
                await page.title(),
                body_text,
            )
            raise RuntimeError(
                "YouTube Music search results did not load in Edge. "
                "Check the open browser tab for consent, sign-in, or "
                "a page error; see MUSIC_NO_SEARCH_RESULTS in the log."
            )

        selected = next(
            (
                item for item in results
                if item.get("videoId") or item.get("href")
            ),
            None,
        )

        if selected is not None:
            href = selected.get("href")
            video_id = selected.get("videoId")

            if not href and video_id:
                href = (
                    "https://music.youtube.com/watch?v="
                    + quote_plus(str(video_id))
                )

            if href:
                if href.startswith("/"):
                    href = "https://music.youtube.com" + href

                await page.goto(
                    href,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            else:
                selected_index = int(selected["index"])
                rows = page.locator(
                    "ytmusic-responsive-list-item-renderer, "
                    "ytmusic-two-row-item-renderer"
                )
                row = rows.nth(selected_index)
                await row.click(timeout=10000)

        else:
            # Some layouts expose playable result controls without a
            # normal watch URL. Click the first actual search result.
            row = page.locator(
                "ytmusic-responsive-list-item-renderer"
            ).first

            title_link = row.locator(
                "a[href], .title"
            ).first

            if await title_link.count():
                await title_link.click(timeout=10000)
            else:
                raise RuntimeError(
                    "Search results appeared, but no playable result "
                    "or clickable title was found."
                )

        await page.wait_for_timeout(1800)

        # If navigation did not start playback, try the player Play control.
        player = page.locator("ytmusic-player-bar")

        try:
            await player.wait_for(state="visible", timeout=10000)
        except Exception as exc:
            raise RuntimeError(
                "A result was selected, but the YouTube Music player "
                "did not appear."
            ) from exc

        pause_button = page.get_by_role(
            "button",
            name="Pause",
        ).first

        if not await pause_button.is_visible():
            play_button = page.get_by_role(
                "button",
                name="Play",
            ).first

            if await play_button.is_visible():
                await play_button.click()
                await page.wait_for_timeout(1000)

        pause_button = page.get_by_role(
            "button",
            name="Pause",
        ).first

        if not await pause_button.is_visible():
            # Keep the failure honest; a loaded page is not proof of audio.
            raise RuntimeError(
                "The track page opened, but active playback could not "
                "be confirmed. Check the Edge player."
            )

        actual_title = ""
        for selector in (
            ".title",
            ".content-info-wrapper .title",
        ):
            title_locator = player.locator(selector).first
            try:
                if await title_locator.count():
                    actual_title = (
                        await title_locator.inner_text(timeout=3000)
                    ).strip()
                    if actual_title:
                        break
            except Exception:
                continue

        if not actual_title:
            actual_title = str(selected.get("title") or query)

        logger.info(
            "MUSIC_PLAYING query=%r title=%r url=%s",
            query,
            actual_title,
            page.url,
        )

        return ToolResult(
            success=True,
            message=f"Now playing: {actual_title}",
            data={
                "query": query,
                "title": actual_title,
                "url": page.url,
                "playing": True,
            },
        )

    async def execute(
        self,
        action: str,
        payload: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        logger.info(
            "TRACE=%s MUSIC_ACTION_START action=%s",
            context.trace_id,
            action,
        )

        try:
            page = await self._get_page()
            await self._ensure_music_page(page)

            if action == "PLAY":
                return await self._play(
                    page,
                    str(payload.get("query", "")),
                )

            if action in {"PAUSE", "STOP"}:
                button = page.get_by_role(
                    "button",
                    name="Pause",
                ).first

                if await button.is_visible():
                    await button.click()
                    return ToolResult(
                        success=True,
                        message="Music paused.",
                        data={"playing": False},
                    )

                return ToolResult(
                    success=True,
                    message="Music is not currently playing.",
                    data={"playing": False},
                )

            if action == "RESUME":
                button = page.get_by_role(
                    "button",
                    name="Play",
                ).first

                if await button.is_visible():
                    await button.click()
                    await page.wait_for_timeout(500)
                    return ToolResult(
                        success=True,
                        message="Resume requested.",
                        data={"playing": True},
                    )

                return ToolResult(
                    success=False,
                    message="The player is not ready to resume.",
                    error_code="PLAYER_NOT_READY",
                )

            if action in {"NEXT", "PREVIOUS"}:
                label = "Next" if action == "NEXT" else "Previous"
                button = page.get_by_role(
                    "button",
                    name=label,
                ).first

                await button.wait_for(
                    state="visible",
                    timeout=5000,
                )
                await button.click()
                await page.wait_for_timeout(700)

                return ToolResult(
                    success=True,
                    message=(
                        "Skipped to the next track."
                        if action == "NEXT"
                        else "Returned to the previous track."
                    ),
                    data={"action": action},
                )

            if action == "SET_VOLUME":
                volume = int(payload["volume"])
                if not 0 <= volume <= 100:
                    raise ValueError("Volume must be between 0 and 100.")

                changed = await page.evaluate(
                    """volume => {
                        const player = document.querySelector(
                            'ytmusic-player-bar'
                        );
                        const slider = player?.querySelector(
                            'tp-yt-paper-slider#volume-slider'
                        );

                        if (!slider) return false;

                        slider.value = volume;
                        slider.dispatchEvent(
                            new Event('input', { bubbles: true })
                        );
                        slider.dispatchEvent(
                            new Event('change', { bubbles: true })
                        );
                        return true;
                    }""",
                    volume,
                )

                if not changed:
                    return ToolResult(
                        success=False,
                        message="YouTube Music volume control was not found.",
                        error_code="VOLUME_CONTROL_NOT_FOUND",
                    )

                return ToolResult(
                    success=True,
                    message=f"Volume set to {volume}%.",
                    data={"volume": volume},
                )

            return ToolResult(
                success=False,
                message=f"Unsupported music action: {action}",
                error_code="UNSUPPORTED_ACTION",
            )

        except Exception as exc:
            logger.exception(
                "TRACE=%s MUSIC_ACTION_FAILED action=%s",
                context.trace_id,
                action,
            )
            return ToolResult(
                success=False,
                message=str(exc) or "Music action failed.",
                error_code="MUSIC_ACTION_FAILED",
            )


TOOL_PLUGIN = MusicPlugin()