"""YouTube Music control through the installed Hello Dodo Edge extension."""

from __future__ import annotations

import logging
from typing import Any

from tools.command_bus import CommandBusError, command_bus
from tools.contracts import ActionSpec, ToolPlugin, ToolSpec
from tools.models import ToolContext, ToolResult

logger = logging.getLogger(__name__)

TOOL_NAME = "play_music"
COMMAND_TIMEOUT_SECONDS = 60.0


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
    """Execute music commands in the user's existing Edge session."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=TOOL_NAME,
            description=(
                "Play songs and control YouTube Music through the "
                "installed Hello Dodo Music Controller Edge extension."
            ),
            version="3.0.0",
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
                    description="Pause playback.",
                    input_schema=_schema(),
                ),
                ActionSpec(
                    name="RESUME",
                    description="Resume playback.",
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
                    description="Pause playback.",
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

    async def execute(
        self,
        action: str,
        payload: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        action = action.strip().upper()
        command_payload = dict(payload)
        command_payload["trace_id"] = context.trace_id

        if action == "PLAY":
            query = command_payload.get("query")

            if not isinstance(query, str) or not query.strip():
                return ToolResult(
                    success=False,
                    message="Please provide a song or artist name.",
                    error_code="INVALID_MUSIC_QUERY",
                )

            command_payload["query"] = query.strip()

        if action == "SET_VOLUME":
            try:
                volume = int(command_payload["volume"])
            except (KeyError, TypeError, ValueError):
                return ToolResult(
                    success=False,
                    message="Volume must be an integer from 0 to 100.",
                    error_code="INVALID_VOLUME",
                )

            if not 0 <= volume <= 100:
                return ToolResult(
                    success=False,
                    message="Volume must be between 0 and 100.",
                    error_code="INVALID_VOLUME",
                )

            command_payload["volume"] = volume

        logger.info(
            "TRACE=%s MUSIC_EXTENSION_COMMAND_START action=%s",
            context.trace_id,
            action,
        )

        try:
            result = await command_bus.dispatch(
                tool_name=TOOL_NAME,
                action=action,
                payload=command_payload,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except CommandBusError as exc:
            logger.warning(
                "TRACE=%s MUSIC_EXTENSION_COMMAND_FAILED action=%s error=%s",
                context.trace_id,
                action,
                exc,
            )

            return ToolResult(
                success=False,
                message=(
                    "Could not get a response from the music extension. "
                    "Make sure Edge is open and Hello Dodo Music Controller "
                    "is enabled, then retry. "
                    f"Details: {exc}"
                ),
                error_code="MUSIC_EXTENSION_UNAVAILABLE",
            )

        success = result.get("success")
        message = result.get("message", "")
        data = result.get("data", {})
        error_code = result.get("error_code")

        if not isinstance(success, bool):
            return ToolResult(
                success=False,
                message="The music extension returned an invalid result.",
                error_code="INVALID_EXTENSION_RESULT",
            )

        if not isinstance(message, str):
            message = str(message)

        if not isinstance(data, dict):
            data = {"result": data}

        if error_code is not None and not isinstance(error_code, str):
            error_code = str(error_code)

        logger.info(
            "TRACE=%s MUSIC_EXTENSION_COMMAND_END action=%s success=%s",
            context.trace_id,
            action,
            success,
        )

        return ToolResult(
            success=success,
            message=message or (
                "Music command completed."
                if success
                else "Music command failed."
            ),
            data=data,
            error_code=error_code,
        )


TOOL_PLUGIN = MusicPlugin()