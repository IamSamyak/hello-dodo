
"""Automatic tool plugin discovery and registration for Hello Dodo."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType
from typing import Any

from tools.contracts import ToolPlugin
from tools.registry import ToolRegistry, tool_registry


logger = logging.getLogger(__name__)

EXCLUDED_MODULES = {
    "__init__",
    "contracts",
    "loader",
    "registry",
    "dispatcher",
    "command_bus",
    "models",
}


def _iter_tool_modules(package_name: str) -> list[ModuleType]:
    """Import eligible top-level modules from the tools package."""

    package = importlib.import_module(package_name)
    package_path = getattr(package, "__path__", None)

    if package_path is None:
        raise RuntimeError(
            f"Tool package '{package_name}' does not expose a module path."
        )

    module_names = sorted(
        module_info.name
        for module_info in pkgutil.iter_modules(package_path)
        if module_info.name not in EXCLUDED_MODULES
        and not module_info.name.startswith("_")
    )

    modules: list[ModuleType] = []

    for module_name in module_names:
        qualified_name = f"{package_name}.{module_name}"

        try:
            module = importlib.import_module(qualified_name)
        except Exception:
            logger.exception(
                "Failed to import tool module: %s",
                qualified_name,
            )
            raise

        modules.append(module)

    return modules


def _validate_plugin(plugin: Any, module_name: str) -> ToolPlugin:
    """Validate a discovered plugin before registration."""

    if not isinstance(plugin, ToolPlugin):
        raise TypeError(
            f"{module_name}.TOOL_PLUGIN must inherit from ToolPlugin."
        )

    spec = plugin.spec

    if not isinstance(spec.name, str) or not spec.name.strip():
        raise ValueError(
            f"{module_name}.TOOL_PLUGIN has an invalid tool name."
        )

    if not isinstance(spec.description, str) or not spec.description.strip():
        raise ValueError(
            f"{module_name}.TOOL_PLUGIN has an invalid description."
        )

    if not isinstance(spec.version, str) or not spec.version.strip():
        raise ValueError(
            f"{module_name}.TOOL_PLUGIN has an invalid version."
        )

    if not spec.actions:
        raise ValueError(
            f"{module_name}.TOOL_PLUGIN must declare at least one action."
        )

    action_names: set[str] = set()

    for action in spec.actions:
        if not action.name or not action.name.strip():
            raise ValueError(
                f"{module_name}.TOOL_PLUGIN contains an empty action name."
            )

        if action.name in action_names:
            raise ValueError(
                f"{module_name}.TOOL_PLUGIN declares duplicate action "
                f"'{action.name}'."
            )

        if not action.description or not action.description.strip():
            raise ValueError(
                f"{module_name}.TOOL_PLUGIN action "
                f"'{action.name}' has no description."
            )

        if not isinstance(action.input_schema, dict):
            raise TypeError(
                f"{module_name}.TOOL_PLUGIN action "
                f"'{action.name}' must have an object input schema."
            )

        action_names.add(action.name)

    return plugin


def discover_tool_plugins(
    package_name: str = "tools",
) -> list[ToolPlugin]:
    """Discover and validate plugins without registering them."""

    discovered: list[ToolPlugin] = []
    discovered_names: set[str] = set()

    for module in _iter_tool_modules(package_name):
        if not hasattr(module, "TOOL_PLUGIN"):
            continue

        plugin = _validate_plugin(
            getattr(module, "TOOL_PLUGIN"),
            module.__name__,
        )

        normalized_name = plugin.spec.name.strip().casefold()

        if normalized_name in discovered_names:
            raise ValueError(
                f"Duplicate discovered tool plugin: {plugin.spec.name}"
            )

        discovered_names.add(normalized_name)
        discovered.append(plugin)

        logger.info(
            "Discovered tool plugin name=%s version=%s module=%s",
            plugin.spec.name,
            plugin.spec.version,
            module.__name__,
        )

    return discovered


def load_tool_plugins(
    *,
    registry: ToolRegistry = tool_registry,
    package_name: str = "tools",
) -> list[str]:
    """Discover and register all available tool plugins."""

    plugins = discover_tool_plugins(package_name)
    registered_names: list[str] = []

    for plugin in plugins:
        registry.register_plugin(plugin)

        registered_names.append(plugin.spec.name)

        logger.info(
            "Registered tool plugin name=%s version=%s",
            plugin.spec.name,
            plugin.spec.version,
        )

    return registered_names