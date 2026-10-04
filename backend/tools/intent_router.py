
"""Intent routing for Hello Dodo's registered tools."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tools.registry import ToolRegistry, tool_registry


@dataclass(frozen=True, slots=True)
class IntentMatch:
    """Resolved tool/action pair for a user message."""

    tool_name: str
    action: str
    confidence: float
    matched_rule: str


@dataclass(frozen=True, slots=True)
class IntentRule:
    """A rule mapping phrases to a registered tool action."""

    tool_name: str
    action: str
    phrases: tuple[str, ...]
    priority: int = 100


class IntentRouter:
    """Resolve explicit phrases to actions from registered tools."""

    def __init__(
        self,
        registry: ToolRegistry = tool_registry,
    ) -> None:
        self._registry = registry
        self._rules: list[IntentRule] = []

    @staticmethod
    def normalize(text: str) -> str:
        """Normalize punctuation, spacing, and case for phrase matching."""
        normalized = text.casefold().strip()
        normalized = re.sub(r"[^\w\s]", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def register_rule(
        self,
        *,
        tool_name: str,
        action: str,
        phrases: tuple[str, ...],
        priority: int = 100,
    ) -> None:
        """Register phrase triggers for an existing tool.

        The tool must already exist in the registry. More specific rules
        should use a lower priority number.
        """
        tool = self._registry.get(tool_name)

        if tool is None:
            raise ValueError(
                f"Cannot register intent: unknown tool '{tool_name}'."
            )

        if not tool.enabled:
            raise ValueError(
                f"Cannot register intent for disabled tool '{tool_name}'."
            )

        if not action.strip():
            raise ValueError("Action cannot be empty.")

        normalized_phrases = tuple(
            dict.fromkeys(
                self.normalize(phrase)
                for phrase in phrases
                if phrase.strip()
            )
        )

        if not normalized_phrases:
            raise ValueError("At least one non-empty phrase is required.")

        if priority < 0:
            raise ValueError("priority cannot be negative.")

        self._rules.append(
            IntentRule(
                tool_name=tool.name,
                action=action.strip(),
                phrases=normalized_phrases,
                priority=priority,
            )
        )

        # Lower priority values are evaluated first. For equal priority,
        # longer phrases are checked first to prefer specific commands.
        self._rules.sort(
            key=lambda rule: (
                rule.priority,
                -max(map(len, rule.phrases)),
                rule.tool_name,
            )
        )

    def resolve(self, message: str) -> IntentMatch | None:
        """Resolve a message using whole-phrase matching.

        Returns None when no registered rule matches, allowing the caller
        to use the normal ChatGPT conversation flow.
        """
        if not isinstance(message, str) or not message.strip():
            return None

        text = self.normalize(message)

        for rule in self._rules:
            tool = self._registry.get(rule.tool_name)

            # A tool disabled after rule registration must not run.
            if tool is None or not tool.enabled:
                continue

            for phrase in rule.phrases:
                # Prevent partial-word matches, e.g. "jobs" in "jobsite".
                pattern = rf"(?<!\w){re.escape(phrase)}(?!\w)"

                if re.search(pattern, text):
                    return IntentMatch(
                        tool_name=rule.tool_name,
                        action=rule.action,
                        confidence=1.0,
                        matched_rule=phrase,
                    )

        return None

    def list_rules(self) -> tuple[IntentRule, ...]:
        """Return a read-only snapshot of registered intent rules."""
        return tuple(self._rules)


intent_router = IntentRouter()