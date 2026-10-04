
from __future__ import annotations

import re
from dataclasses import replace
from threading import RLock
from typing import Any

from tools.models import ToolDefinition


class ToolRegistry:
    """Thread-safe registry for legacy tools and contract plugins."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._aliases: dict[str, str] = {}
        self._lock = RLock()

    @staticmethod
    def normalize_name(name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tool name must be a non-empty string.")

        normalized = re.sub(
            r"[\s-]+",
            "_",
            name.strip().casefold(),
        )

        return normalized

    def _rebuild_aliases(self) -> None:
        aliases: dict[str, str] = {}

        for canonical_name, definition in self._tools.items():
            for alias in (canonical_name, *definition.aliases):
                normalized_alias = self.normalize_name(alias)

                existing = aliases.get(normalized_alias)

                if (
                    existing is not None
                    and existing != canonical_name
                ):
                    raise ValueError(
                        f"Tool alias {alias!r} conflicts with "
                        f"registered tool {existing!r}."
                    )

                aliases[normalized_alias] = canonical_name

        self._aliases = aliases

    def register(
        self,
        definition: ToolDefinition,
        *,
        replace_existing: bool = False,
    ) -> None:
        if not isinstance(definition, ToolDefinition):
            raise TypeError(
                "register() requires a ToolDefinition."
            )

        name = self.normalize_name(definition.name)

        if not callable(definition.handler):
            raise ValueError(
                f"Tool {name!r} must have a callable handler."
            )

        normalized_definition = replace(
            definition,
            name=name,
            aliases=tuple(
                self.normalize_name(alias)
                for alias in definition.aliases
            ),
        )

        with self._lock:
            previous = self._tools.get(name)

            if previous is not None and not replace_existing:
                # Idempotent registration is allowed only for the same
                # underlying handler/plugin, not a silent replacement.
                if (
                    previous.handler is definition.handler
                    and previous.plugin is definition.plugin
                ):
                    return

                raise ValueError(
                    f"Tool {name!r} is already registered."
                )

            old_tools = self._tools.copy()
            self._tools[name] = normalized_definition

            try:
                self._rebuild_aliases()
            except Exception:
                self._tools = old_tools
                self._rebuild_aliases()
                raise

    def register_plugin(
        self,
        plugin: Any,
        *,
        replace_existing: bool = False,
    ) -> None:
        """Register an implementation of the ToolPlugin contract."""
        from tools.contracts import ToolPlugin

        if not isinstance(plugin, ToolPlugin):
            raise TypeError(
                "register_plugin() requires a ToolPlugin implementation."
            )

        spec = plugin.spec

        if not spec.name or not spec.name.strip():
            raise ValueError("Plugin tool name cannot be empty.")

        definition = ToolDefinition(
            name=spec.name,
            description=spec.description,
            handler=plugin.execute,
            enabled=True,
            aliases=tuple(spec.aliases),
            spec=spec,
            plugin=plugin,
        )

        self.register(
            definition,
            replace_existing=replace_existing,
        )

    def get(self, name: str) -> ToolDefinition | None:
        """Resolve a canonical name or registered alias."""
        normalized = self.normalize_name(name)

        with self._lock:
            canonical_name = self._aliases.get(normalized)

            if canonical_name is None:
                return None

            return self._tools.get(canonical_name)

    def get_plugin(self, name: str) -> Any | None:
        definition = self.get(name)

        if definition is None:
            return None

        return definition.plugin

    def list_tools(
        self,
        enabled_only: bool = True,
    ) -> list[ToolDefinition]:
        with self._lock:
            definitions = list(self._tools.values())

        if enabled_only:
            definitions = [
                definition
                for definition in definitions
                if definition.enabled
            ]

        return sorted(
            definitions,
            key=lambda definition: definition.name,
        )

    def list_catalog(
        self,
        enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Return JSON-compatible tool metadata for decision prompts."""
        catalog: list[dict[str, Any]] = []

        for definition in self.list_tools(enabled_only=enabled_only):
            item: dict[str, Any] = {
                "name": definition.name,
                "description": definition.description,
                "aliases": list(definition.aliases),
                "contract_version": None,
                "actions": [],
            }

            if definition.spec is not None:
                item["contract_version"] = definition.spec.version

                item["actions"] = [
                    {
                        "name": action.name,
                        "description": action.description,
                        "input_schema": action.input_schema,
                    }
                    for action in definition.spec.actions
                ]

            catalog.append(item)

        return catalog

    def set_enabled(
        self,
        name: str,
        enabled: bool,
    ) -> bool:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a boolean.")

        normalized = self.normalize_name(name)

        with self._lock:
            canonical_name = self._aliases.get(normalized)

            if canonical_name is None:
                return False

            definition = self._tools[canonical_name]

            self._tools[canonical_name] = replace(
                definition,
                enabled=enabled,
            )

            return True

    def unregister(self, name: str) -> bool:
        """Remove a tool and its aliases."""
        normalized = self.normalize_name(name)

        with self._lock:
            canonical_name = self._aliases.get(normalized)

            if canonical_name is None:
                return False

            del self._tools[canonical_name]
            self._rebuild_aliases()

            return True


tool_registry = ToolRegistry()