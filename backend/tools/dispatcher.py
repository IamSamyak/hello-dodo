
"""Generic dispatcher for Hello Dodo tools."""

from __future__ import annotations

import inspect
import logging
import time
import uuid
from typing import Any

from tools.intent_router import IntentRouter, intent_router
from tools.models import ToolContext, ToolResult
from tools.registry import ToolRegistry, tool_registry

logger = logging.getLogger(__name__)


class ToolDispatcher:
    """Resolve and execute registered Hello Dodo tools."""

    def __init__(
        self,
        *,
        registry: ToolRegistry = tool_registry,
        router: IntentRouter = intent_router,
    ) -> None:
        self._registry = registry
        self._router = router

    @staticmethod
    def _normalize_result(result: Any) -> ToolResult:
        if isinstance(result, ToolResult):
            return result

        if isinstance(result, dict):
            return ToolResult(
                success=bool(result.get("success", True)),
                message=str(
                    result.get("message", "Command completed.")
                ),
                data=result.get("data", {}),
                error_code=result.get("error_code"),
            )

        if isinstance(result, str):
            return ToolResult(success=True, message=result)

        return ToolResult(
            success=False,
            message="The tool returned an invalid response.",
            error_code="INVALID_TOOL_RESULT",
        )

    async def dispatch_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        source: str = "voice",
        trace_id: str = "-",
    ) -> ToolResult:
        """Execute a GPT-selected registered tool after validation."""
        started = time.monotonic()
        request_id = str(uuid.uuid4())

        logger.info(
            "TRACE=%s GPT_TOOL_DISPATCH_START tool=%s request_id=%s",
            trace_id,
            tool_name,
            request_id,
        )

        if not isinstance(tool_name, str) or not tool_name.strip():
            return ToolResult(
                success=False,
                message="GPT selected an invalid tool name.",
                error_code="INVALID_TOOL_NAME",
            )

        if not isinstance(arguments, dict):
            return ToolResult(
                success=False,
                message="GPT returned invalid tool arguments.",
                error_code="INVALID_TOOL_ARGUMENTS",
            )

        action = arguments.get("action")
        if not isinstance(action, str) or not action.strip():
            return ToolResult(
                success=False,
                message="The selected tool requires a valid action.",
                error_code="MISSING_TOOL_ACTION",
            )

        tool = self._registry.get(tool_name)

        if tool is None:
            logger.warning(
                "TRACE=%s GPT_TOOL_NOT_REGISTERED tool=%s",
                trace_id,
                tool_name,
            )
            return ToolResult(
                success=False,
                message=f"The requested tool '{tool_name}' is unavailable.",
                error_code="TOOL_NOT_REGISTERED",
            )

        if not tool.enabled:
            return ToolResult(
                success=False,
                message=f"The requested tool '{tool_name}' is disabled.",
                error_code="TOOL_DISABLED",
            )

        supplied_payload = arguments.get("payload", {})
        if not isinstance(supplied_payload, dict):
            return ToolResult(
                success=False,
                message="The tool payload must be an object.",
                error_code="INVALID_TOOL_PAYLOAD",
            )

        payload = dict(supplied_payload)
        payload.update(
            {
                "request_id": request_id,
                "source": source,
                "trace_id": trace_id,
                "matched_rule": "gpt_tool_decision",
            }
        )

        try:
            result: Any = tool.handler(
                action=action,
                payload=payload,
            )

            if inspect.isawaitable(result):
                result = await result

            tool_result = self._normalize_result(result)

            logger.info(
                "TRACE=%s GPT_TOOL_DISPATCH_END tool=%s action=%s "
                "request_id=%s success=%s error_code=%s elapsed=%.2fs",
                trace_id,
                tool_name,
                action,
                request_id,
                tool_result.success,
                tool_result.error_code,
                time.monotonic() - started,
            )

            return tool_result

        except Exception:
            logger.exception(
                "TRACE=%s GPT_TOOL_DISPATCH_FAILED tool=%s action=%s "
                "request_id=%s elapsed=%.2fs",
                trace_id,
                tool_name,
                action,
                request_id,
                time.monotonic() - started,
            )
            return ToolResult(
                success=False,
                message="The selected tool failed during execution.",
                error_code="TOOL_EXECUTION_FAILED",
            )

    async def dispatch_message(
        self,
        message: str,
        *,
        source: str = "voice",
        trace_id: str = "-",
    ) -> ToolResult | None:
        """Keep the existing phrase-based routing available."""
        started = time.monotonic()

        logger.info(
            "TRACE=%s INTENT_RESOLVE_START source=%s message=%r",
            trace_id,
            source,
            message[:200],
        )

        try:
            match = self._router.resolve(message)
        except Exception:
            logger.exception(
                "TRACE=%s INTENT_RESOLVE_FAILED",
                trace_id,
            )
            return ToolResult(
                success=False,
                message="I couldn't resolve the local command.",
                error_code="INTENT_RESOLVE_FAILED",
            )

        if match is None:
            logger.info(
                "TRACE=%s INTENT_NO_MATCH source=%s",
                trace_id,
                source,
            )
            return None

        logger.info(
            "TRACE=%s INTENT_MATCH tool=%s action=%s rule=%r",
            trace_id,
            match.tool_name,
            match.action,
            match.matched_rule,
        )

        return await self.dispatch_tool_call(
            match.tool_name,
            {"action": match.action},
            source=source,
            trace_id=trace_id,
        )


tool_dispatcher = ToolDispatcher()