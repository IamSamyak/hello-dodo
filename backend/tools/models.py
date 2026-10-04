
"""Shared contracts for Hello Dodo tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Metadata supplied to a tool during execution."""

    request_id: str
    source: str = "voice"


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Standardized result returned by every tool."""

    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the result to a JSON-compatible dictionary."""
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Metadata and handler for a registered tool."""

    name: str
    description: str
    handler: Any
    enabled: bool = True
    aliases: tuple[str, ...] = ()