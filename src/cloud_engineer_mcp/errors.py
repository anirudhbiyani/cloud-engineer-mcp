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
