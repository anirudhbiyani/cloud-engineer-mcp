"""Per-tool allow/deny + rate-limit + dry-run + audit policy engine.

Sits in front of every tool call. Deterministic, side-effect-free decision
function plus an append-only JSONL audit sink. Off by default; opt in via
`policy.enabled: true` in config.

Decision flow
-------------
For each tool call::

    1. If `enabled` is false → ALLOW. No further checks.
    2. Match `deny` patterns. First match wins → DENY.
    3. If `allow` non-empty and no pattern matches → DENY (whitelist mode).
    4. Match rate-limit patterns. If any bucket is empty → RATE_LIMITED.
    5. If `dry_run` is true → ALLOW_DRY_RUN (caller returns a stub).
    6. Otherwise → ALLOW.

Every decision is written to the audit log when `audit.enabled` is true.

Patterns are fnmatch globs against the **namespaced tool name** (e.g.
`aws_prod__delete_resource`). Patterns are case-sensitive.
"""

from __future__ import annotations

import fnmatch
import json
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from cloud_engineer_mcp.observability.logging import get_logger

if TYPE_CHECKING:
    from cloud_engineer_mcp.config import PolicyConfig

log = get_logger("policy")


class PolicyDecision(Enum):
    ALLOW = "allow"
    ALLOW_DRY_RUN = "allow_dry_run"
    DENY = "deny"
    RATE_LIMITED = "rate_limited"


@dataclass
class PolicyResult:
    decision: PolicyDecision
    reason: str = ""
    matched_pattern: str | None = None
    rate_limit_per_minute: int | None = None


class _SlidingWindowLimiter:
    """Per-pattern sliding-window counter. Bounded memory: keeps timestamps
    within the most recent 60 seconds.
    """

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            events = self._events[key]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self._per_minute:
                return False
            events.append(now)
            return True


class PolicyEngine:
    """Decides ALLOW / DENY / RATE_LIMITED for each tool call.

    Thread-safe: rate-limit buckets use locks. Audit writes use a file lock
    via O_APPEND, which is atomic for small writes on POSIX.
    """

    def __init__(self, config: PolicyConfig) -> None:
        self._enabled = config.enabled
        self._dry_run = config.dry_run
        self._deny = list(config.deny)
        self._allow = list(config.allow)
        self._rate_limits = [
            (rule.pattern, rule.per_minute, _SlidingWindowLimiter(rule.per_minute))
            for rule in config.rate_limits
        ]
        self._audit_enabled = config.audit.enabled
        self._audit_path = Path(config.audit.path) if config.audit.enabled else None
        if self._audit_path is not None:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def evaluate(self, namespaced_name: str) -> PolicyResult:
        """Pure-function decision. Side effects (rate-limit increment, audit
        write) live in ``check()``.
        """
        if not self._enabled:
            return PolicyResult(PolicyDecision.ALLOW)

        for pattern in self._deny:
            if fnmatch.fnmatchcase(namespaced_name, pattern):
                return PolicyResult(
                    PolicyDecision.DENY,
                    reason=f"matches deny pattern {pattern!r}",
                    matched_pattern=pattern,
                )

        if self._allow and not any(fnmatch.fnmatchcase(namespaced_name, p) for p in self._allow):
            return PolicyResult(
                PolicyDecision.DENY,
                reason="no allow pattern matched (whitelist mode)",
            )

        if self._dry_run:
            return PolicyResult(PolicyDecision.ALLOW_DRY_RUN, reason="dry-run mode")

        return PolicyResult(PolicyDecision.ALLOW)

    def check(
        self,
        namespaced_name: str,
        session_id: str | None = None,
    ) -> PolicyResult:
        """Run the full check including rate-limit accounting and audit.

        Use this from request handlers. Use ``evaluate`` from tests when you
        only need the decision without bumping rate-limit counters.
        """
        result = self.evaluate(namespaced_name)
        if result.decision in (PolicyDecision.ALLOW, PolicyDecision.ALLOW_DRY_RUN):
            rl = self._check_rate_limits(namespaced_name)
            if rl is not None:
                result = rl

        if self._audit_enabled:
            self._append_audit(namespaced_name, session_id, result)
        return result

    def _check_rate_limits(self, namespaced_name: str) -> PolicyResult | None:
        for pattern, per_minute, limiter in self._rate_limits:
            if fnmatch.fnmatchcase(namespaced_name, pattern) and not limiter.allow(pattern):
                return PolicyResult(
                    PolicyDecision.RATE_LIMITED,
                    reason=f"matches {pattern!r} ({per_minute}/min)",
                    matched_pattern=pattern,
                    rate_limit_per_minute=per_minute,
                )
        return None

    def _append_audit(
        self,
        namespaced_name: str,
        session_id: str | None,
        result: PolicyResult,
    ) -> None:
        if self._audit_path is None:
            return
        entry = {
            "ts": time.time(),
            "tool": namespaced_name,
            "session_id": session_id,
            "decision": result.decision.value,
            "reason": result.reason,
            "matched_pattern": result.matched_pattern,
        }
        line = (json.dumps(entry) + "\n").encode("utf-8")
        # O_APPEND is atomic for writes ≤ PIPE_BUF (4096B) on POSIX. Our entries
        # are far smaller, so concurrent threads (and even processes) won't
        # interleave.
        fd = os.open(self._audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
