"""ContextExtractor: builds a search query from conversation context."""

from __future__ import annotations


class ContextExtractor:
    def __init__(self, max_tokens: int = 512) -> None:
        self._max_tokens = max_tokens
        self._max_chars = max_tokens * 4  # approximate: 1 token ~ 4 chars

    def extract_query(
        self,
        user_message: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        tool_call_history: list[str] | None = None,
        action: str | None = None,
        resource_type: str | None = None,
    ) -> str:
        """Build a search query string for the tool selector.

        Prioritizes (in descending weight):
        1. Structured intent (action + resource_type), repeated to up-weight
        2. Latest user message
        3. Recent tool names (momentum bias)
        4. Keywords from recent assistant messages
        """
        parts: list[str] = []

        # Structured intent goes first AND is repeated once so it contributes
        # ~2x to the bag-of-words style embedding query.
        if action or resource_type:
            intent = " ".join(p for p in (action, resource_type) if p)
            parts.append(intent)
            parts.append(intent)

        if user_message:
            parts.append(user_message)

        if tool_call_history:
            recent_tools = tool_call_history[-5:]
            tool_text = " ".join(name.replace("__", " ").replace("_", " ") for name in recent_tools)
            parts.append(f"Related tools: {tool_text}")

        if conversation_history:
            for msg in reversed(conversation_history[-5:]):
                if msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if content:
                        parts.append(content[:200])

        result = " ".join(parts)
        if len(result) > self._max_chars:
            result = result[: self._max_chars]

        return result
