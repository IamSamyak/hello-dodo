
"""Shared asynchronous command bus for Hello Dodo tools."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_COMMAND_TTL_SECONDS = 180.0
DEFAULT_MAX_PENDING_COMMANDS = 100


class CommandBusError(RuntimeError):
    """Base exception for command bus errors."""


class CommandTimeoutError(CommandBusError):
    """Raised when a command does not finish in time."""


class CommandCapacityError(CommandBusError):
    """Raised when the command bus reaches its capacity."""


@dataclass(frozen=True, slots=True)
class ToolCommand:
    """A command delivered to a tool executor."""

    command_id: str
    tool_name: str
    action: str
    payload: dict[str, Any]
    created_at: float
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        """Return the extension-compatible JSON representation."""
        return {
            "command_id": self.command_id,
            "tool_name": self.tool_name,
            "action": self.action,
            "payload": self.payload,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


@dataclass(slots=True)
class _PendingCommand:
    """Internal state for a command awaiting completion."""

    future: asyncio.Future[dict[str, Any]]
    created_at: float
    expires_at: float


class CommandBus:
    """Queue commands and correlate executor results by command ID.

    The queue is in-memory and is not durable across process restarts.
    """

    def __init__(
        self,
        *,
        max_pending: int = DEFAULT_MAX_PENDING_COMMANDS,
        command_ttl: float = DEFAULT_COMMAND_TTL_SECONDS,
    ) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be at least one.")
        if command_ttl <= 0:
            raise ValueError("command_ttl must be greater than zero.")

        self.max_pending = max_pending
        self.command_ttl = command_ttl
        self._queue: asyncio.Queue[ToolCommand] = asyncio.Queue()
        self._pending: dict[str, _PendingCommand] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _trace_id(payload: dict[str, Any] | None) -> str:
        return str((payload or {}).get("trace_id") or "-")

    async def dispatch(
        self,
        *,
        tool_name: str,
        action: str,
        payload: dict[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Enqueue a command and await its matching executor result."""
        trace_id = self._trace_id(payload)
        started = time.monotonic()

        if not tool_name.strip():
            raise ValueError("tool_name cannot be empty.")
        if not action.strip():
            raise ValueError("action cannot be empty.")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero.")

        loop = asyncio.get_running_loop()
        command_id = str(uuid.uuid4())
        created_at = time.time()
        expires_at = created_at + self.command_ttl

        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        command = ToolCommand(
            command_id=command_id,
            tool_name=tool_name,
            action=action,
            payload=dict(payload or {}),
            created_at=created_at,
            expires_at=expires_at,
        )

        async with self._lock:
            self._prune_expired_locked()

            if len(self._pending) >= self.max_pending:
                logger.error(
                    "TRACE=%s COMMAND_CAPACITY_REACHED "
                    "tool=%s action=%s pending=%s maximum=%s",
                    trace_id,
                    tool_name,
                    action,
                    len(self._pending),
                    self.max_pending,
                )
                raise CommandCapacityError(
                    "Hello Dodo command capacity reached. Try again shortly."
                )

            self._pending[command_id] = _PendingCommand(
                future=future,
                created_at=created_at,
                expires_at=expires_at,
            )

            try:
                self._queue.put_nowait(command)
            except asyncio.QueueFull as exc:
                self._pending.pop(command_id, None)
                logger.exception(
                    "TRACE=%s COMMAND_QUEUE_FAILED tool=%s action=%s",
                    trace_id,
                    tool_name,
                    action,
                )
                raise CommandCapacityError(
                    "Hello Dodo command queue is full."
                ) from exc

            queued_count = self._queue.qsize()
            pending_count = len(self._pending)

        logger.info(
            "TRACE=%s COMMAND_QUEUED id=%s tool=%s action=%s "
            "queued=%s pending=%s timeout=%.2fs",
            trace_id,
            command_id,
            tool_name,
            action,
            queued_count,
            pending_count,
            timeout,
        )

        try:
            result = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=timeout,
            )

            logger.info(
                "TRACE=%s COMMAND_RESULT_RETURNED id=%s tool=%s "
                "action=%s success=%s elapsed=%.2fs",
                trace_id,
                command_id,
                tool_name,
                action,
                result.get("success"),
                time.monotonic() - started,
            )

            return result

        except asyncio.TimeoutError as exc:
            logger.error(
                "TRACE=%s COMMAND_WAIT_TIMEOUT id=%s tool=%s action=%s "
                "timeout=%.2fs elapsed=%.2fs",
                trace_id,
                command_id,
                tool_name,
                action,
                timeout,
                time.monotonic() - started,
            )

            raise CommandTimeoutError(
                f"Command '{action}' did not complete within {timeout:g}s."
            ) from exc

        except asyncio.CancelledError:
            logger.warning(
                "TRACE=%s COMMAND_CALL_CANCELLED id=%s tool=%s action=%s "
                "elapsed=%.2fs",
                trace_id,
                command_id,
                tool_name,
                action,
                time.monotonic() - started,
            )
            raise

        finally:
            async with self._lock:
                pending = self._pending.pop(command_id, None)

                if pending is not None and not pending.future.done():
                    pending.future.cancel()

            logger.info(
                "TRACE=%s COMMAND_WAITER_CLEANED id=%s action=%s",
                trace_id,
                command_id,
                action,
            )

    async def next_command(self) -> dict[str, Any] | None:
        """Return the next command that is still eligible for execution."""
        while True:
            try:
                command = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None

            try:
                async with self._lock:
                    pending = self._pending.get(command.command_id)

                    if pending is None:
                        logger.warning(
                            "COMMAND_SKIPPED id=%s reason=not_pending",
                            command.command_id,
                        )
                        continue

                    if pending.future.done():
                        self._pending.pop(command.command_id, None)
                        logger.warning(
                            "COMMAND_SKIPPED id=%s reason=future_completed",
                            command.command_id,
                        )
                        continue

                    if time.time() >= pending.expires_at:
                        self._pending.pop(command.command_id, None)
                        if not pending.future.done():
                            pending.future.cancel()

                        logger.warning(
                            "COMMAND_SKIPPED id=%s reason=expired",
                            command.command_id,
                        )
                        continue

                    command_data = command.to_dict()

                logger.info(
                    "TRACE=%s COMMAND_DELIVERED id=%s tool=%s action=%s",
                    self._trace_id(command.payload),
                    command.command_id,
                    command.tool_name,
                    command.action,
                )

                return command_data

            finally:
                self._queue.task_done()
                
                
    async def next_command_for_tool(
        self,
        tool_name: str,
    ) -> dict[str, Any] | None:
        """Deliver the next queued command matching a specific tool.

        Commands for other tools remain in the queue.
        """
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name cannot be empty.")

        async with self._lock:
            queued_commands: list[ToolCommand] = []
            selected_command: ToolCommand | None = None
            now = time.time()

            while True:
                try:
                    command = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                self._queue.task_done()

                pending = self._pending.get(command.command_id)

                if (
                    pending is None
                    or pending.future.done()
                    or now >= pending.expires_at
                ):
                    if pending is not None:
                        self._pending.pop(command.command_id, None)

                        if not pending.future.done():
                            pending.future.cancel()

                    continue

                if (
                    selected_command is None
                    and command.tool_name == tool_name
                ):
                    selected_command = command
                else:
                    queued_commands.append(command)

            for command in queued_commands:
                self._queue.put_nowait(command)

            if selected_command is None:
                return None

            logger.info(
                "TRACE=%s COMMAND_DELIVERED id=%s tool=%s action=%s",
                self._trace_id(selected_command.payload),
                selected_command.command_id,
                selected_command.tool_name,
                selected_command.action,
            )

            return selected_command.to_dict()

    async def complete(
        self,
        command_id: str,
        result: dict[str, Any],
    ) -> bool:
        """Resolve a waiting command with its matching executor result."""
        if not isinstance(command_id, str) or not command_id.strip():
            logger.warning("COMMAND_RESULT_REJECTED reason=invalid_id")
            return False

        if not isinstance(result, dict):
            logger.warning(
                "COMMAND_RESULT_REJECTED id=%s reason=invalid_result_type",
                command_id,
            )
            return False

        async with self._lock:
            pending = self._pending.get(command_id)

            if pending is None or pending.future.done():
                logger.warning(
                    "COMMAND_RESULT_REJECTED id=%s "
                    "reason=unknown_or_already_completed",
                    command_id,
                )
                return False

            if time.time() >= pending.expires_at:
                self._pending.pop(command_id, None)

                if not pending.future.done():
                    pending.future.cancel()

                logger.warning(
                    "COMMAND_RESULT_REJECTED id=%s reason=expired",
                    command_id,
                )
                return False

            pending.future.set_result(result)

        logger.info(
            "COMMAND_COMPLETED id=%s success=%s",
            command_id,
            result.get("success"),
        )

        return True

    def _prune_expired_locked(self) -> None:
        """Remove expired or completed waiters. Caller must hold _lock."""
        now = time.time()

        expired_ids = [
            command_id
            for command_id, pending in self._pending.items()
            if now >= pending.expires_at or pending.future.done()
        ]

        for command_id in expired_ids:
            pending = self._pending.pop(command_id, None)

            if pending is None:
                continue

            if not pending.future.done():
                pending.future.cancel()

            logger.warning(
                "COMMAND_PENDING_PRUNED id=%s reason=%s",
                command_id,
                "expired" if now >= pending.expires_at else "future_done",
            )

    async def stats(self) -> dict[str, int]:
        """Return basic queue metrics for health and diagnostics."""
        async with self._lock:
            return {
                "pending_commands": len(self._pending),
                "queued_commands": self._queue.qsize(),
                "max_pending_commands": self.max_pending,
            }


command_bus = CommandBus()