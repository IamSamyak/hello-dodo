
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from tools.models import ToolContext, ToolResult


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """Public contract and input schema for one tool action."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Discoverable metadata for a tool."""

    name: str
    description: str
    version: str
    actions: tuple[ActionSpec, ...]
    aliases: tuple[str, ...] = ()

    def get_action(self, action_name: str) -> ActionSpec | None:
        return next(
            (
                action
                for action in self.actions
                if action.name == action_name
            ),
            None,
        )


class ContractValidationError(ValueError):
    """Raised when a tool action or its input violates the contract."""


def validate_payload(
    payload: dict[str, Any],
    schema: dict[str, Any],
    path: str = "payload",
) -> None:
    """Validate the supported JSON Schema subset used by tool contracts."""
    if not isinstance(payload, dict):
        raise ContractValidationError(
            f"{path} must be an object."
        )

    if schema.get("type", "object") != "object":
        raise ContractValidationError(
            "Tool action schemas must have an object at the root."
        )

    properties = schema.get("properties", {})
    required = schema.get("required", [])
    allow_extra = schema.get("additionalProperties", False)

    if not isinstance(properties, dict):
        raise ContractValidationError(
            f"{path} has an invalid properties schema."
        )

    if not isinstance(required, list):
        raise ContractValidationError(
            f"{path} has an invalid required list."
        )

    missing = [
        name
        for name in required
        if name not in payload
    ]

    if missing:
        raise ContractValidationError(
            f"{path} is missing required field(s): "
            + ", ".join(missing)
        )

    if allow_extra is False:
        unexpected = [
            name
            for name in payload
            if name not in properties
        ]

        if unexpected:
            raise ContractValidationError(
                f"{path} contains unsupported field(s): "
                + ", ".join(unexpected)
            )

    for name, value in payload.items():
        field_schema = properties.get(name)

        if field_schema is None:
            if isinstance(allow_extra, dict):
                _validate_value(
                    value,
                    allow_extra,
                    f"{path}.{name}",
                )
            continue

        _validate_value(
            value,
            field_schema,
            f"{path}.{name}",
        )


def _validate_value(
    value: Any,
    schema: dict[str, Any],
    path: str,
) -> None:
    expected_type = schema.get("type")

    type_checks = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: (
            isinstance(item, int) and not isinstance(item, bool)
        ),
        "number": lambda item: (
            isinstance(item, (int, float))
            and not isinstance(item, bool)
        ),
        "boolean": lambda item: isinstance(item, bool),
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "null": lambda item: item is None,
    }

    if expected_type is not None:
        check = type_checks.get(expected_type)

        if check is None:
            raise ContractValidationError(
                f"{path} uses unsupported schema type "
                f"{expected_type!r}."
            )

        if not check(value):
            raise ContractValidationError(
                f"{path} must be of type {expected_type}."
            )

    if "enum" in schema and value not in schema["enum"]:
        raise ContractValidationError(
            f"{path} must be one of {schema['enum']}."
        )

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ContractValidationError(
                f"{path} is shorter than the minimum length."
            )

        if len(value) > schema.get("maxLength", float("inf")):
            raise ContractValidationError(
                f"{path} exceeds the maximum length."
            )

    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        if value < schema.get("minimum", float("-inf")):
            raise ContractValidationError(
                f"{path} is below the minimum value."
            )

        if value > schema.get("maximum", float("inf")):
            raise ContractValidationError(
                f"{path} exceeds the maximum value."
            )

    if expected_type == "object":
        validate_payload(value, schema, path)

    if expected_type == "array":
        item_schema = schema.get("items")

        if item_schema:
            for index, item in enumerate(value):
                _validate_value(
                    item,
                    item_schema,
                    f"{path}[{index}]",
                )


class ToolPlugin(ABC):
    """Interface every new Hello Dodo tool must implement."""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Return the tool's name, actions, schemas and metadata."""
        raise NotImplementedError

    @abstractmethod
    async def execute(
        self,
        action: str,
        payload: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        """Execute one validated action."""
        raise NotImplementedError

    async def health_check(self) -> dict[str, Any]:
        """Return dependency health without executing an action."""
        return {
            "healthy": True,
            "tool": self.spec.name,
            "version": self.spec.version,
        }

    def validate_action(
        self,
        action_name: str,
        payload: dict[str, Any],
    ) -> ActionSpec:
        """Validate the requested action and its arguments."""
        action_spec = self.spec.get_action(action_name)

        if action_spec is None:
            raise ContractValidationError(
                f"Action {action_name!r} is not supported by "
                f"tool {self.spec.name!r}."
            )

        validate_payload(
            payload,
            action_spec.input_schema,
        )

        return action_spec