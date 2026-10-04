
"""Validated tool dispatcher for Hello Dodo."""

from __future__ import annotations

import inspect
import logging
import uuid
from typing import Any

from tools.contracts import ContractValidationError
from tools.intent_router import IntentRouter, intent_router
from tools.models import ToolContext, ToolResult
from tools.registry import ToolRegistry, tool_registry


logger = logging.getLogger(__name__)


class ToolDispatcher:
    """Validate and dispatch calls to registered tools."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        router: IntentRouter | None = None,
    ) -> None:
        self.registry = registry or tool_registry
        self.router = router or intent_router

    @staticmethod
    def _normalize_result(result: Any) -> ToolResult:
        """Convert supported handler results into ToolResult."""

        if isinstance(result, ToolResult):
            return result

        if isinstance(result, str):
            return ToolResult(
                success=True,
                message=result,
            )

        if isinstance(result, dict):
            success = result.get("success")

            if not isinstance(success, bool):
                return ToolResult(
                    success=False,
                    message="Tool returned a result without a valid success flag.",
                    error_code="INVALID_TOOL_RESULT",
                )

            message = result.get("message", "")

            if not isinstance(message, str):
                message = str(message)

            data = result.get("data", {})
            if not isinstance(data, dict):
                data = {"result": data}

            error_code = result.get("error_code")
            if error_code is not None and not isinstance(error_code, str):
                error_code = str(error_code)

            return ToolResult(
                success=success,
                message=message,
                data=data,
                error_code=error_code,
            )

        return ToolResult(
            success=False,
            message="Tool returned an unsupported result type.",
            error_code="INVALID_TOOL_RESULT",
        )

    @staticmethod
    def _validate_arguments(
        arguments: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Validate the common tool-call envelope."""

        unexpected = set(arguments) - {"action", "payload"}

        if unexpected:
            raise ContractValidationError(
                "Unexpected tool argument(s): "
                + ", ".join(sorted(unexpected))
            )

        action = arguments.get("action")

        if not isinstance(action, str) or not action.strip():
            raise ContractValidationError(
                "Tool action must be a non-empty string."
            )

        payload = arguments.get("payload", {})

        if not isinstance(payload, dict):
            raise ContractValidationError(
                "Tool payload must be a JSON object."
            )

        return action.strip(), payload

    async def dispatch_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        source: str = "voice",
        trace_id: str = "-",
    ) -> ToolResult:
        """Validate a tool call and execute its registered handler."""

        request_id = str(uuid.uuid4())

        if not isinstance(tool_name, str) or not tool_name.strip():
            return ToolResult(
                success=False,
                message="Tool name must be a non-empty string.",
                error_code="INVALID_TOOL_NAME",
            )

        if not isinstance(arguments, dict):
            return ToolResult(
                success=False,
                message="Tool arguments must be a JSON object.",
                error_code="INVALID_TOOL_ARGUMENTS",
            )

        definition = self.registry.get(tool_name.strip())

        if definition is None:
            logger.warning(
                "TRACE=%s TOOL_REJECTED unknown_tool=%s request_id=%s",
                trace_id,
                tool_name,
                request_id,
            )
            return ToolResult(
                success=False,
                message=f"Tool '{tool_name}' is not registered.",
                error_code="UNKNOWN_TOOL",
            )

        if not definition.enabled:
            return ToolResult(
                success=False,
                message=f"Tool '{definition.name}' is disabled.",
                error_code="TOOL_DISABLED",
            )

        try:
            action, payload = self._validate_arguments(arguments)

            spec = getattr(definition, "spec", None)
            plugin = getattr(definition, "plugin", None)

            if spec is not None:
                allowed_actions = {
                    item.name: item
                    for item in spec.actions
                }

                if action not in allowed_actions:
                    raise ContractValidationError(
                        f"Action '{action}' is not declared by "
                        f"tool '{definition.name}'."
                    )

            if plugin is not None:
                plugin.validate_action(action, payload)

            context = ToolContext(
                request_id=request_id,
                source=source,
                trace_id=trace_id,
            )

            logger.info(
                "TRACE=%s TOOL_EXECUTION_START "
                "tool=%s action=%s request_id=%s source=%s",
                trace_id,
                definition.name,
                action,
                request_id,
                source,
            )

            if plugin is not None:
                result = plugin.execute(
                    action=action,
                    payload=dict(payload),
                    context=context,
                )
            else:
                # Compatibility path for tools that still use legacy
                # handlers while the plugin migration is in progress.
                execution_payload = dict(payload)
                execution_payload.update(
                    {
                        "request_id": request_id,
                        "source": source,
                        "trace_id": trace_id,
                        "matched_rule": "gpt_tool_decision",
                    }
                )

                result = definition.handler(
                    action=action,
                    payload=execution_payload,
                )

            if inspect.isawaitable(result):
                result = await result

            normalized = self._normalize_result(result)

            logger.info(
                "TRACE=%s TOOL_EXECUTION_END "
                "tool=%s action=%s request_id=%s success=%s",
                trace_id,
                definition.name,
                action,
                request_id,
                normalized.success,
            )

            return normalized

        except ContractValidationError as exc:
            logger.warning(
                "TRACE=%s TOOL_CONTRACT_REJECTED "
                "tool=%s request_id=%s reason=%s",
                trace_id,
                definition.name,
                request_id,
                exc,
            )
            return ToolResult(
                success=False,
                message=str(exc),
                error_code="CONTRACT_VALIDATION_FAILED",
            )

        except Exception:
            logger.exception(
                "TRACE=%s TOOL_EXECUTION_FAILED "
                "tool=%s request_id=%s",
                trace_id,
                definition.name,
                request_id,
            )
            return ToolResult(
                success=False,
                message="The requested tool failed during execution.",
                error_code="TOOL_EXECUTION_FAILED",
            )

    async def dispatch_message(
        self,
        message: str,
        *,
        source: str = "voice",
        trace_id: str = "-",
    ) -> ToolResult:
        """Route a phrase-based request through the existing intent router."""

        if not isinstance(message, str) or not message.strip():
            return ToolResult(
                success=False,
                message="Message cannot be empty.",
                error_code="EMPTY_MESSAGE",
            )

        match = self.router.match(message)

        if match is None:
            return ToolResult(
                success=False,
                message="No registered intent matched the request.",
                error_code="NO_MATCHING_INTENT",
            )

        return await self.dispatch_tool_call(
            match.tool_name,
            {"action": match.action, "payload": {}},
            source=source,
            trace_id=trace_id,
        )


tool_dispatcher = ToolDispatcher()