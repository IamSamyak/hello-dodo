
"""Central registry for Hello Dodo tool definitions."""

from __future__ import annotations

import re
from collections.abc import Callable

from tools.models import ToolDefinition


class ToolRegistry:
    """Register and retrieve tools without editing the API layer."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._aliases: dict[str, str] = {}

    @staticmethod
    def _normalize(value: str) -> str:
        """Normalize a tool name or alias."""
        value = value.casefold().strip()
        value = re.sub(r"[\s-]+", "_", value)
        return value

    def register(
        self,
        *,
        name: str,
        description: str,
        handler: Callable,
        aliases: tuple[str, ...] = (),
        enabled: bool = True,
    ) -> ToolDefinition:
        """Register a tool and its optional aliases."""
        normalized_name = self._normalize(name)

        if not normalized_name:
            raise ValueError("Tool name cannot be empty.")

        if not callable(handler):
            raise TypeError("Tool handler must be callable.")

        if normalized_name in self._tools:
            raise ValueError(
                f"Tool is already registered: {normalized_name}"
            )

        normalized_aliases = tuple(
            dict.fromkeys(
                self._normalize(alias)
                for alias in aliases
                if alias.strip()
            )
        )

        for alias in normalized_aliases:
            if alias == normalized_name:
                raise ValueError(
                    f"Alias duplicates the tool name: {alias}"
                )

            if alias in self._aliases or alias in self._tools:
                raise ValueError(
                    f"Tool name or alias is already registered: {alias}"
                )

        definition = ToolDefinition(
            name=normalized_name,
            description=description.strip(),
            handler=handler,
            enabled=enabled,
            aliases=normalized_aliases,
        )

        self._tools[normalized_name] = definition

        for alias in normalized_aliases:
            self._aliases[alias] = normalized_name

        return definition

    def get(self, name: str) -> ToolDefinition | None:
        """Find a tool by its registered name or alias."""
        normalized = self._normalize(name)
        canonical_name = self._aliases.get(normalized, normalized)
        return self._tools.get(canonical_name)

    def list_tools(
        self,
        *,
        enabled_only: bool = True,
    ) -> tuple[ToolDefinition, ...]:
        """Return registered tools in registration order."""
        tools = self._tools.values()

        if enabled_only:
            tools = (
                tool for tool in tools if tool.enabled
            )

        return tuple(tools)

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a registered tool."""
        tool = self.get(name)

        if tool is None:
            return False

        self._tools[tool.name] = ToolDefinition(
            name=tool.name,
            description=tool.description,
            handler=tool.handler,
            enabled=enabled,
            aliases=tool.aliases,
        )
        return True


tool_registry = ToolRegistry()