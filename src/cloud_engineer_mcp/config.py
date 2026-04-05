"""Pydantic configuration models and YAML loading for cloud_engineer_mcp."""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _interpolate_env(value: str) -> str:
    """Expand ${VAR_NAME} references in a string from the environment."""

    def _replace(match: re.Match[str]) -> str:
        var = match.group(1)
        return os.environ.get(var, match.group(0))

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _deep_interpolate(obj: object) -> object:
    """Recursively interpolate environment variables in config data."""
    if isinstance(obj, str):
        return _interpolate_env(obj)
    if isinstance(obj, dict):
        return {k: _deep_interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_interpolate(v) for v in obj]
    return obj


class StdioTransportConfig(BaseModel):
    enabled: bool = True


class HttpTransportConfig(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])


class TransportConfig(BaseModel):
    stdio: StdioTransportConfig = Field(default_factory=StdioTransportConfig)
    http: HttpTransportConfig = Field(default_factory=HttpTransportConfig)


class ServerConfig(BaseModel):
    name: str = "cloud-engineer-mcp"
    version: str = "1.0.0"
    transports: TransportConfig = Field(default_factory=TransportConfig)


class SelectorConfig(BaseModel):
    model_name: str = "all-MiniLM-L6-v2"
    top_k: int = 15
    min_similarity: float = 0.15
    cache_embeddings: bool = True
    embedding_cache_path: str = ".cloud-engineer-mcp/embeddings_cache.npz"
    context_max_tokens: int = 512


class BackendConfig(BaseModel):
    display_name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    restart_on_failure: bool = True
    max_restarts: int = 3
    startup_timeout_seconds: int = 30
    health_check_interval_seconds: int = 60


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"
    file: str | None = None


class HealthConfig(BaseModel):
    enabled: bool = True
    endpoint: str = "/health"
    include_backends: bool = True


class RateLimitConfig(BaseModel):
    enabled: bool = True
    requests_per_minute: int = 100


class AWSDiscoveryConfig(BaseModel):
    enabled: bool = True
    default_region: str = "us-east-1"
    mcp_server: str = "awslabs.ccapi-mcp-server@latest"
    exclude_profiles: list[str] = Field(default_factory=list)
    startup_timeout_seconds: int = 60


class AzureDiscoveryConfig(BaseModel):
    enabled: bool = True
    mcp_command: str = "npx -y @azure/mcp@latest server start"
    exclude_subscriptions: list[str] = Field(default_factory=list)
    startup_timeout_seconds: int = 60


class GCPDiscoveryConfig(BaseModel):
    enabled: bool = True
    mcp_server: str = "@google-cloud/gcloud-mcp@latest"
    exclude_projects: list[str] = Field(default_factory=list)
    startup_timeout_seconds: int = 60


class DiscoveryConfig(BaseModel):
    enabled: bool = True
    aws: AWSDiscoveryConfig = Field(default_factory=AWSDiscoveryConfig)
    azure: AzureDiscoveryConfig = Field(default_factory=AzureDiscoveryConfig)
    gcp: GCPDiscoveryConfig = Field(default_factory=GCPDiscoveryConfig)


class CloudEngineerConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    selector: SelectorConfig = Field(default_factory=SelectorConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    backends: dict[str, BackendConfig] = Field(default_factory=dict)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> CloudEngineerConfig:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        data = _deep_interpolate(data)
        return cls.model_validate(data)

    def redacted(self) -> dict:
        """Return config dict with sensitive env values redacted."""
        data = self.model_dump()
        for backend in data.get("backends", {}).values():
            env = backend.get("env", {})
            for key in env:
                upper = key.upper()
                if any(
                    s in upper for s in ("SECRET", "KEY", "TOKEN", "PASSWORD", "CREDENTIAL")
                ):
                    env[key] = "***REDACTED***"
        return data
