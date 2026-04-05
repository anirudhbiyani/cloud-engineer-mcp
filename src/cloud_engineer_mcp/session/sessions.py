"""SessionManager: per-session state for conversation-aware tool selection."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from cloud_engineer_mcp.observability.logging import get_logger

log = get_logger("session.sessions")

PIN_DECAY_CALLS = 3
SCORE_BOOST = 0.3
MAX_MESSAGES = 20
MAX_TOOL_HISTORY = 10
CLEANUP_INTERVAL_SECONDS = 60


@dataclass
class Session:
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    conversation_messages: list[dict[str, str]] = field(default_factory=list)
    tool_call_history: list[str] = field(default_factory=list)
    pinned_tools: dict[str, int] = field(default_factory=dict)
    context: str | None = None
    cloud_providers: list[str] | None = None

    def add_message(self, role: str, content: str) -> None:
        """Append a message and keep only the last MAX_MESSAGES."""
        self.last_active = time.time()
        self.conversation_messages.append({"role": role, "content": content})
        if len(self.conversation_messages) > MAX_MESSAGES:
            self.conversation_messages = self.conversation_messages[-MAX_MESSAGES:]

    def record_tool_call(self, tool_name: str) -> None:
        """Record that a tool was called. Keep last MAX_TOOL_HISTORY."""
        self.last_active = time.time()
        self.tool_call_history.append(tool_name)
        if len(self.tool_call_history) > MAX_TOOL_HISTORY:
            self.tool_call_history = self.tool_call_history[-MAX_TOOL_HISTORY:]

    def pin_backend_tools(self, tool_names: list[str]) -> None:
        """Pin tools from the same backend so they stay in subsequent listings."""
        for name in tool_names:
            self.pinned_tools[name] = PIN_DECAY_CALLS

    def decay_pins(self) -> None:
        """Decrease pin counts and remove expired pins. Called on each tools/list."""
        expired = []
        for name in self.pinned_tools:
            self.pinned_tools[name] -= 1
            if self.pinned_tools[name] <= 0:
                expired.append(name)
        for name in expired:
            del self.pinned_tools[name]

    def get_score_boosts(self) -> dict[str, float]:
        """Return score boosts for currently pinned tools."""
        return {name: SCORE_BOOST for name in self.pinned_tools}

    def set_context(self, context: str, cloud_providers: list[str] | None = None) -> None:
        """Update the session context for tool selection."""
        self.context = context
        self.cloud_providers = cloud_providers
        self.add_message("user", context)


class SessionManager:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._sessions: dict[str, Session] = {}
        self._ttl = ttl_seconds
        self._cleanup_task: asyncio.Task | None = None

    def get_or_create(self, session_id: str) -> Session:
        """Get existing session or create a new one."""
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(session_id=session_id)
            log.debug("session.created", session_id=session_id)
        return self._sessions[session_id]

    def start_cleanup_loop(self) -> None:
        """Start a background task that periodically cleans up expired sessions."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    def stop_cleanup_loop(self) -> None:
        """Cancel the background cleanup task."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None

    async def _cleanup_loop(self) -> None:
        """Periodically remove expired sessions."""
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            self.cleanup_expired()

    def cleanup_expired(self) -> int:
        """Remove sessions older than TTL. Returns count removed."""
        now = time.time()
        expired = [
            sid
            for sid, session in self._sessions.items()
            if now - session.last_active > self._ttl
        ]
        for sid in expired:
            del self._sessions[sid]
            log.debug("session.expired", session_id=sid)
        return len(expired)

    @property
    def active_count(self) -> int:
        return len(self._sessions)
