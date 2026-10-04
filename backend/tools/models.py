
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Execution context supplied to a tool handler."""

    request_id: str
    source: str = "voice"
    trace_id: str = "-"


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Standard result returned by every tool execution."""

    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """
    Backward-compatible registered tool definition.

    Legacy handlers continue to use handler.
    Contract-based plugins additionally provide spec and plugin.
    """

    name: str
    description: str
    handler: Any
    enabled: bool = True
    aliases: tuple[str, ...] = ()
    spec: Any | None = None
    plugin: Any | None = None