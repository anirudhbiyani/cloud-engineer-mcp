"""SessionManager: per-session state for conversation-aware tool selection."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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
    # Optional structured intent. When set, weights the embedding query toward
    # the named action/resource type.
    action: str | None = None
    resource_type: str | None = None

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

    def set_context(
        self,
        context: str,
        cloud_providers: list[str] | None = None,
        action: str | None = None,
        resource_type: str | None = None,
    ) -> None:
        """Update the session context for tool selection.

        `action` and `resource_type` are optional structured hints (e.g.
        action="create", resource_type="s3 bucket"). When present they are
        weighted into the embedding query for higher selection precision.
        """
        self.context = context
        self.cloud_providers = cloud_providers
        self.action = action
        self.resource_type = resource_type
        self.add_message("user", context)


class SessionManager:
    def __init__(
        self,
        ttl_seconds: int = 3600,
        persist_path: str | None = None,
        persist_interval_seconds: float = 30.0,
    ) -> None:
        self._sessions: dict[str, Session] = {}
        self._ttl = ttl_seconds
        self._cleanup_task: asyncio.Task[None] | None = None
        self._persist_task: asyncio.Task[None] | None = None
        self._persist_path = persist_path
        self._persist_interval = persist_interval_seconds
        if persist_path:
            self._load_from_disk()

    def get_or_create(self, session_id: str) -> Session:
        """Get existing session or create a new one."""
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(session_id=session_id)
            log.debug("session.created", session_id=session_id)
        return self._sessions[session_id]

    def start_cleanup_loop(self) -> None:
        """Start background loops for cleanup and (optional) persistence."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        if self._persist_path and (self._persist_task is None or self._persist_task.done()):
            self._persist_task = asyncio.create_task(self._persist_loop())

    def stop_cleanup_loop(self) -> None:
        """Cancel the background tasks and flush sessions once more if persisting."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        if self._persist_task is not None:
            self._persist_task.cancel()
            self._persist_task = None
        if self._persist_path:
            self._save_to_disk()

    async def _cleanup_loop(self) -> None:
        """Periodically remove expired sessions."""
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            self.cleanup_expired()

    async def _persist_loop(self) -> None:
        """Periodically write active sessions to disk."""
        while True:
            await asyncio.sleep(self._persist_interval)
            try:
                self._save_to_disk()
            except Exception as exc:
                log.warning("session.persist_failed", error=str(exc))

    def _save_to_disk(self) -> None:
        if not self._persist_path:
            return
        path = Path(self._persist_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {sid: asdict(s) for sid, s in self._sessions.items()}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, path)
        log.debug("session.persisted", count=len(payload), path=str(path))

    def _load_from_disk(self) -> None:
        if not self._persist_path:
            return
        path = Path(self._persist_path)
        if not path.exists():
            return
        try:
            raw: dict[str, dict[str, Any]] = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("session.load_failed", error=str(exc))
            return
        now = time.time()
        for sid, data in raw.items():
            # Drop sessions that already expired on disk.
            if now - data.get("last_active", 0) > self._ttl:
                continue
            try:
                self._sessions[sid] = Session(**data)
            except TypeError as exc:
                # Schema drift: skip incompatible entries.
                log.warning("session.skip_incompatible", session_id=sid, error=str(exc))
        log.info("session.loaded_from_disk", count=len(self._sessions), path=str(path))

    def cleanup_expired(self) -> int:
        """Remove sessions older than TTL. Returns count removed."""
        now = time.time()
        expired = [
            sid for sid, session in self._sessions.items() if now - session.last_active > self._ttl
        ]
        for sid in expired:
            del self._sessions[sid]
            log.debug("session.expired", session_id=sid)
        return len(expired)

    @property
    def active_count(self) -> int:
        return len(self._sessions)
