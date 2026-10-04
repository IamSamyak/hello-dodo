
"""Job Application tool plugin for Hello Dodo."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from service_manager import prepare_job_action
from tools.command_bus import CommandBus, CommandTimeoutError, command_bus
from tools.contracts import ActionSpec, ToolPlugin, ToolSpec
from tools.intent_router import IntentRouter, intent_router
from tools.models import ToolContext, ToolResult
from tools.registry import ToolRegistry, tool_registry


logger = logging.getLogger(__name__)

TOOL_NAME = "job_application"

ACTION_MESSAGES = {
    "NAUKRI_OPEN_JOBS": "Naukri Recommended Jobs khol diya.",
    "LINKEDIN_GET_JOBS": "LinkedIn jobs fetch karne ka command bhej diya.",
    "LINKEDIN_APPLY_EASY_APPLY": (
        "LinkedIn Easy Apply automation ka command bhej diya."
    ),
    "JOB_APPLICATION_DASHBOARD": "Job Application Dashboard khol diya.",
}


def _action_specs() -> tuple[ActionSpec, ...]:
    """Return the public action contracts for the job application tool."""

    return (
        ActionSpec(
            name="NAUKRI_OPEN_JOBS",
            description="Open recommended Naukri jobs.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        ),
        ActionSpec(
            name="LINKEDIN_GET_JOBS",
            description="Fetch LinkedIn jobs through the browser extension.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        ),
        ActionSpec(
            name="LINKEDIN_APPLY_EASY_APPLY",
            description="Run LinkedIn Easy Apply automation.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        ),
        ActionSpec(
            name="JOB_APPLICATION_DASHBOARD",
            description="Open the Job Application Dashboard.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        ),
    )


async def execute_job_application(
    action: str,
    *,
    payload: dict[str, Any] | None = None,
    bus: CommandBus = command_bus,
) -> ToolResult:
    """Execute local dashboard actions or dispatch extension commands."""

    payload = dict(payload or {})
    trace_id = str(payload.get("trace_id") or "-")
    started = time.monotonic()

    logger.info(
        "TRACE=%s JOB_ACTION_START action=%s",
        trace_id,
        action,
    )

    if action not in ACTION_MESSAGES:
        logger.error(
            "TRACE=%s JOB_ACTION_UNSUPPORTED action=%s",
            trace_id,
            action,
        )
        return ToolResult(
            success=False,
            message="Unsupported job application action.",
            error_code="UNSUPPORTED_ACTION",
        )

    try:
        logger.info(
            "TRACE=%s SERVICE_PREPARE_START action=%s",
            trace_id,
            action,
        )

        dashboard_url = await asyncio.to_thread(
            prepare_job_action,
            action,
            trace_id,
        )

        logger.info(
            "TRACE=%s SERVICE_PREPARE_END action=%s elapsed=%.2fs",
            trace_id,
            action,
            time.monotonic() - started,
        )

    except Exception as exc:
        logger.exception(
            "TRACE=%s SERVICE_PREPARE_FAILED action=%s",
            trace_id,
            action,
        )
        return ToolResult(
            success=False,
            message=f"Required service could not start: {exc}",
            error_code="SERVICE_START_FAILED",
        )

    if action == "JOB_APPLICATION_DASHBOARD":
        logger.info(
            "TRACE=%s DASHBOARD_OPEN_REQUESTED url=%s elapsed=%.2fs",
            trace_id,
            dashboard_url,
            time.monotonic() - started,
        )
        return ToolResult(
            success=True,
            message=ACTION_MESSAGES[action],
            data={
                "url": dashboard_url,
                "execution": "local",
            },
        )

    payload["trace_id"] = trace_id

    try:
        logger.info(
            "TRACE=%s COMMAND_DISPATCH_START action=%s",
            trace_id,
            action,
        )

        result = await bus.dispatch(
            tool_name=TOOL_NAME,
            action=action,
            payload=payload,
        )

        logger.info(
            "TRACE=%s COMMAND_DISPATCH_END action=%s elapsed=%.2fs",
            trace_id,
            action,
            time.monotonic() - started,
        )

    except CommandTimeoutError:
        logger.exception(
            "TRACE=%s COMMAND_TIMEOUT action=%s",
            trace_id,
            action,
        )
        return ToolResult(
            success=False,
            message=(
                "The command timed out. Check that Edge is open and "
                "the Hello Dodo extension is enabled and responding."
            ),
            error_code="COMMAND_TIMEOUT",
        )

    except Exception as exc:
        logger.exception(
            "TRACE=%s COMMAND_FAILED action=%s type=%s",
            trace_id,
            action,
            type(exc).__name__,
        )
        return ToolResult(
            success=False,
            message=(
                f"The Job Application command failed: "
                f"{type(exc).__name__}: {exc}"
            ),
            error_code="COMMAND_FAILED",
        )

    if isinstance(result, dict) and result.get("success") is False:
        return ToolResult(
            success=False,
            message=str(
                result.get("error")
                or "The extension reported that the command failed."
            ),
            data=result,
            error_code="EXTENSION_COMMAND_FAILED",
        )

    return ToolResult(
        success=True,
        message=ACTION_MESSAGES[action],
        data=result if isinstance(result, dict) else {"result": result},
    )


class JobApplicationPlugin(ToolPlugin):
    """Contract-based adapter for existing job application actions."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=TOOL_NAME,
            description=(
                "Open Naukri jobs, fetch LinkedIn jobs, run LinkedIn "
                "Easy Apply, or open the Job Application Dashboard."
            ),
            version="1.0.0",
            actions=_action_specs(),
            aliases=("jobs", "job-applications"),
        )

    async def execute(
        self,
        action: str,
        payload: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        """Delegate execution to the existing implementation."""

        execution_payload = dict(payload)
        execution_payload["trace_id"] = context.trace_id

        return await execute_job_application(
            action,
            payload=execution_payload,
        )


TOOL_PLUGIN = JobApplicationPlugin()


def register_job_application_tool(
    *,
    registry: ToolRegistry = tool_registry,
    router: IntentRouter = intent_router,
    bus: CommandBus = command_bus,
) -> None:
    """Register job application voice-intent rules.

    Tool registration itself is owned by the plugin loader.
    The parameters are retained for compatibility with existing callers.
    """

    del registry, bus

    rules = (
        (
            "JOB_APPLICATION_DASHBOARD",
            (
                "open job application dashboard",
                "start job application dashboard",
                "start the job application dashboard",
                "open the job application dashboard",
                "job application dashboard",
                "job application dashboard kholo",
                "application dashboard kholo",
                "job dashboard kholo",
                "dashboard khol do",
                "dashboard kholo",
                "dashboard dikhao",
            ),
            10,
        ),
        (
            "LINKEDIN_APPLY_EASY_APPLY",
            (
                "linkedin easy apply start karo",
                "linkedin easy apply",
                "linkedin jobs pe apply karo",
                "linkedin apply for jobs",
                "linkedin apply jobs",
            ),
            20,
        ),
        (
            "LINKEDIN_GET_JOBS",
            (
                "linkedin se jobs lao",
                "linkedin jobs fetch karo",
                "linkedin jobs dikhao",
                "get linkedin jobs",
                "fetch linkedin jobs",
                "linkedin jobs lao",
            ),
            30,
        ),
        (
            "NAUKRI_OPEN_JOBS",
            (
                "naukri recommended jobs kholo",
                "naukri applications start karo",
                "naukri jobs dikhao",
                "naukri kholo",
                "open naukri",
                "start naukri",
            ),
            40,
        ),
    )

    existing_rules = {
        (rule.tool_name, rule.action, rule.phrases)
        for rule in router.list_rules()
    }

    for action, phrases, priority in rules:
        normalized_phrases = tuple(
            router.normalize(phrase) for phrase in phrases
        )
        rule_key = (TOOL_NAME, action, normalized_phrases)

        if rule_key in existing_rules:
            continue

        router.register_rule(
            tool_name=TOOL_NAME,
            action=action,
            phrases=phrases,
            priority=priority,
        )

        existing_rules.add(rule_key)

        logger.info(
            "Registered intent tool=%s action=%s phrases=%s",
            TOOL_NAME,
            action,
            normalized_phrases,
        )


TOOL_PLUGIN = JobApplicationPlugin()