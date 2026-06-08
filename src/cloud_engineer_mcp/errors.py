"""Custom exception hierarchy for cloud_engineer_mcp."""

from __future__ import annotations


class CloudEngineerError(Exception):
    """Base exception for all cloud-engineer-mcp errors."""


class ConfigError(CloudEngineerError):
    """Invalid configuration."""


class BackendError(CloudEngineerError):
    """Base for backend-related errors."""

    def __init__(self, backend_id: str, message: str) -> None:
        self.backend_id = backend_id
        super().__init__(f"[{backend_id}] {message}")


class BackendStartupError(BackendError):
    """Backend failed to start."""


class BackendUnavailableError(BackendError):
    """Backend is not in READY state."""


class BackendTimeoutError(BackendError):
    """Backend did not respond in time."""


class ToolNotFoundError(CloudEngineerError):
    """Requested tool does not exist in any backend."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Tool not found: {tool_name}")


class SelectorError(CloudEngineerError):
    """Error in the tool selection engine."""


class CredentialError(CloudEngineerError):
    """No valid cloud credentials found for any discovered account."""

    def __init__(self, providers: list[str], details: str = "") -> None:
        self.providers = providers
        msg = f"No valid credentials found for providers: {', '.join(providers)}"
        if details:
            msg += f". {details}"
        super().__init__(msg)


class PolicyDeniedError(CloudEngineerError):
    """A policy explicitly denied the tool call."""

    def __init__(self, tool_name: str, reason: str) -> None:
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"Tool call '{tool_name}' denied by policy: {reason}")


class PolicyRateLimitedError(CloudEngineerError):
    """A per-tool rate limit was exceeded."""

    def __init__(self, tool_name: str, pattern: str, per_minute: int) -> None:
        self.tool_name = tool_name
        self.pattern = pattern
        self.per_minute = per_minute
        super().__init__(
            f"Tool call '{tool_name}' rate-limited by policy "
            f"(pattern={pattern!r}, limit={per_minute}/min)"
        )
