"""Pydantic configuration models and YAML loading for cloud_engineer_mcp."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


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
    # Empty by default: no browser origin can reach /mcp unless explicitly
    # listed. A safe default for a process holding delegated cloud credentials.
    # See SECURITY.md.
    cors_origins: list[str] = Field(default_factory=list)


class TransportConfig(BaseModel):
    stdio: StdioTransportConfig = Field(default_factory=StdioTransportConfig)
    http: HttpTransportConfig = Field(default_factory=HttpTransportConfig)


class ServerConfig(BaseModel):
    name: str = "cloud-engineer-mcp"
    version: str = "1.0.0"
    transports: TransportConfig = Field(default_factory=TransportConfig)


class SelectorConfig(BaseModel):
    # Which ranking backend to use. "embedding" loads sentence-transformers
    # (~80MB) and runs dense matvec; "bm25" is pure-Python BM25, zero deps,
    # ~5pp lower Recall@15 on average.
    backend: str = "embedding"
    model_name: str = "all-MiniLM-L6-v2"
    top_k: int = 15
    min_similarity: float = 0.15
    cache_embeddings: bool = True
    embedding_cache_path: str = ".cloud-engineer-mcp/embeddings_cache.npz"
    context_max_tokens: int = 512


class BackendConfig(BaseModel):
    """Configuration for a single backend MCP server.

    Two transports are supported:

    - **stdio** (default): the gateway spawns ``command args...`` and talks
      JSON-RPC over the subprocess's stdin/stdout. The traditional model.
    - **http**: the gateway connects to a remote streamable-HTTP MCP server
      at ``url`` and authenticates with ``headers``. Used by Google's
      managed-MCP endpoints, AWS's managed aws-mcp / aws-knowledge-mcp, and
      any remote MCP-as-a-service. Header values support ``${ENV_VAR}``
      interpolation so tokens don't end up in config files.

    Exactly one of (command, url) must be set.
    """

    display_name: str
    transport: str = "stdio"
    # stdio
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # http (remote MCP)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    http_timeout_seconds: float = 30.0
    # When `auth_refresh_command` is set, the gateway runs it at start time
    # to mint a Bearer token (e.g. `gcloud auth print-access-token`) and
    # re-runs it on every 401 response. Lets short-lived OAuth tokens
    # rotate without restarting the backend.
    auth_refresh_command: list[str] = Field(default_factory=list)
    auth_header_name: str = "Authorization"
    auth_header_template: str = "Bearer {token}"
    enabled: bool = True
    restart_on_failure: bool = True
    max_restarts: int = 3
    startup_timeout_seconds: int = 30
    health_check_interval_seconds: int = 60
    # Exponential-backoff base for restart attempts (n=1 → ~base, n=2 → ~2*base ...).
    restart_backoff_base_seconds: float = 1.0
    restart_backoff_max_seconds: float = 60.0

    @field_validator("url")
    @classmethod
    def _validate_url_scheme(cls, value: str) -> str:
        """Reject non-http(s) backend URLs.

        The url is handed directly to the HTTP client, so an unconstrained
        scheme (``file://``, ``ftp://``, ``gopher://`` ...) would let a config
        author read local files or pivot to internal services (SSRF). Empty is
        allowed — stdio backends carry no url.
        """
        if not value:
            return value
        parsed = urlparse(value)
        if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES:
            raise ValueError(
                f"backend url must use http or https, got {parsed.scheme or '(none)'!r}: "
                f"{value!r}. Other schemes are rejected to prevent local-file reads / SSRF."
            )
        if not parsed.netloc:
            raise ValueError(f"backend url must include a host: {value!r}")
        return value


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
    # awslabs.aws-iac-mcp-server (formerly ccapi-mcp-server, which AWS Labs
    # has deprecated). Covers CloudFormation + CDK + Cloud Control API.
    mcp_server: str = "awslabs.aws-iac-mcp-server@latest"
    # Optional specialized AWS Labs servers to start ALONGSIDE the primary
    # one. Each becomes its own backend per profile. Empty by default for
    # backwards compatibility. See docs/AWS-EXTRAS.md for the catalog.
    extra_servers: list[str] = Field(default_factory=list)
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


class KubernetesDiscoveryConfig(BaseModel):
    """Auto-discover kubeconfig contexts and run a kube MCP server per context.

    Default is the Red Hat-backed Go-native ``containers/kubernetes-mcp-server``:
    single binary, no subprocess overhead, native OpenShift support. Swap the
    ``mcp_command`` to ``npx -y mcp-server-kubernetes@latest`` for the
    Node-based Flux159 implementation, or to your own command.
    """

    enabled: bool = False
    mcp_command: str = "npx -y kubernetes-mcp-server@latest"
    exclude_contexts: list[str] = Field(default_factory=list)
    # Path override; defaults to the kubeconfig the kubectl CLI would pick.
    kubeconfig_path: str | None = None
    startup_timeout_seconds: int = 60


class CloudflareDiscoveryConfig(BaseModel):
    """Single-backend Cloudflare integration.

    The Cloudflare MCP server is run once and authenticates via an API token
    in the env var named by `token_env`. We do not auto-discover accounts.
    """

    enabled: bool = False
    mcp_command: str = "npx -y @cloudflare/mcp-server-cloudflare@latest"
    token_env: str = "CLOUDFLARE_API_TOKEN"
    startup_timeout_seconds: int = 60


class DigitalOceanDiscoveryConfig(BaseModel):
    """Single-backend DigitalOcean integration via a personal access token."""

    enabled: bool = False
    mcp_command: str = "npx -y @digitalocean/mcp-server@latest"
    token_env: str = "DIGITALOCEAN_TOKEN"
    startup_timeout_seconds: int = 60


class AzureDevOpsDiscoveryConfig(BaseModel):
    """Microsoft's Azure DevOps MCP server.

    Authenticates via ``az login`` (same path Azure MCP uses). Auto-discovers
    nothing on its own — set ``organizations`` to the ADO org slug(s) you want
    one backend per. Each org becomes a separate subprocess.
    """

    enabled: bool = False
    mcp_command: str = "npx -y @azure-devops/mcp@latest"
    organizations: list[str] = Field(default_factory=list)
    startup_timeout_seconds: int = 60


class PlaywrightDiscoveryConfig(BaseModel):
    """Microsoft's Playwright MCP server: browser/web interaction.

    Single backend, no auth, no per-account discovery. Useful when an agent
    needs to test or scrape a web UI as part of a multi-cloud workflow.
    """

    enabled: bool = False
    mcp_command: str = "npx -y @playwright/mcp@latest"
    startup_timeout_seconds: int = 60


# ---------------------------------------------------------------------------
# Remote MCP integrations (HTTPS endpoints, not subprocesses)
# ---------------------------------------------------------------------------


class GitHubRemoteDiscoveryConfig(BaseModel):
    """GitHub's hosted MCP server (`https://api.githubcopilot.com/mcp`).

    Auth is a GitHub Personal Access Token in the env var named by
    ``token_env``. The gateway forwards it as `Authorization: Bearer <token>`.
    """

    enabled: bool = False
    url: str = "https://api.githubcopilot.com/mcp"
    token_env: str = "GITHUB_TOKEN"
    startup_timeout_seconds: int = 30


class MicrosoftLearnRemoteDiscoveryConfig(BaseModel):
    """Microsoft Learn MCP server (`https://learn.microsoft.com/api/mcp`).

    No authentication required. Single anonymous backend; gives the agent
    documentation lookup against learn.microsoft.com.
    """

    enabled: bool = False
    url: str = "https://learn.microsoft.com/api/mcp"
    startup_timeout_seconds: int = 30


class AWSKnowledgeRemoteDiscoveryConfig(BaseModel):
    """AWS's managed documentation MCP (`https://knowledge-mcp.global.api.aws`)."""

    enabled: bool = False
    url: str = "https://knowledge-mcp.global.api.aws"
    startup_timeout_seconds: int = 30


class AWSManagedRemoteDiscoveryConfig(BaseModel):
    """AWS's general-purpose managed MCP proxy
    (`https://aws-mcp.us-east-1.api.aws/mcp`).

    Wraps comprehensive AWS API support with documentation. Most use cases
    overlap with running multiple awslabs servers locally; this gives a
    single remote endpoint instead. When AWS adds auth to this proxy, set
    ``token_env`` (Bearer header) or ``auth_refresh_command`` to mint tokens.
    """

    enabled: bool = False
    url: str = "https://aws-mcp.us-east-1.api.aws/mcp"
    token_env: str | None = None
    startup_timeout_seconds: int = 30


class GCPRemoteService(BaseModel):
    """A single Google Cloud managed-MCP endpoint to enable."""

    name: str
    url: str


class GCPRemoteDiscoveryConfig(BaseModel):
    """Google Cloud's 40+ managed-MCP endpoints.

    These are HTTPS streamable-HTTP MCP servers Google hosts under
    ``*.googleapis.com/mcp``. Authentication is a short-lived OAuth token
    obtained from ``gcloud auth print-access-token`` at startup; refresh is
    handled by re-running discovery (or restarting the gateway). For
    long-lived deployments use a service account key and a workload-identity
    setup; see docs/REMOTE-MCP.md.
    """

    enabled: bool = False
    services: list[GCPRemoteService] = Field(default_factory=list)
    # Override how the token is acquired. The default invokes `gcloud auth
    # print-access-token`. To use a fixed token, set token_env instead.
    token_command: list[str] = Field(
        default_factory=lambda: ["gcloud", "auth", "print-access-token"]
    )
    token_env: str | None = None
    startup_timeout_seconds: int = 30


class DiscoveryConfig(BaseModel):
    enabled: bool = True
    aws: AWSDiscoveryConfig = Field(default_factory=AWSDiscoveryConfig)
    azure: AzureDiscoveryConfig = Field(default_factory=AzureDiscoveryConfig)
    gcp: GCPDiscoveryConfig = Field(default_factory=GCPDiscoveryConfig)
    kubernetes: KubernetesDiscoveryConfig = Field(default_factory=KubernetesDiscoveryConfig)
    cloudflare: CloudflareDiscoveryConfig = Field(default_factory=CloudflareDiscoveryConfig)
    digitalocean: DigitalOceanDiscoveryConfig = Field(default_factory=DigitalOceanDiscoveryConfig)
    azure_devops: AzureDevOpsDiscoveryConfig = Field(default_factory=AzureDevOpsDiscoveryConfig)
    playwright: PlaywrightDiscoveryConfig = Field(default_factory=PlaywrightDiscoveryConfig)
    # Remote (HTTPS) MCP integrations.
    github_remote: GitHubRemoteDiscoveryConfig = Field(default_factory=GitHubRemoteDiscoveryConfig)
    microsoft_learn: MicrosoftLearnRemoteDiscoveryConfig = Field(
        default_factory=MicrosoftLearnRemoteDiscoveryConfig
    )
    aws_knowledge: AWSKnowledgeRemoteDiscoveryConfig = Field(
        default_factory=AWSKnowledgeRemoteDiscoveryConfig
    )
    aws_managed: AWSManagedRemoteDiscoveryConfig = Field(
        default_factory=AWSManagedRemoteDiscoveryConfig
    )
    gcp_remote: GCPRemoteDiscoveryConfig = Field(default_factory=GCPRemoteDiscoveryConfig)


class PolicyRateLimitRule(BaseModel):
    """A single per-tool rate limit. `pattern` is fnmatch glob over the namespaced name."""

    pattern: str
    per_minute: int


class PolicyAuditConfig(BaseModel):
    """JSONL audit log of every policy decision."""

    enabled: bool = False
    path: str = ".cloud-engineer-mcp/audit.log"


class PolicyConfig(BaseModel):
    """Allow/deny + dry-run + rate-limit + audit policy applied to every tool call.

    Deny patterns match first. Allow patterns are checked second (empty list
    means "allow anything not explicitly denied"). Patterns are fnmatch globs
    against the namespaced tool name, e.g. `aws_prod__delete_resource`.
    """

    enabled: bool = False
    dry_run: bool = False
    deny: list[str] = Field(default_factory=list)
    allow: list[str] = Field(default_factory=list)
    rate_limits: list[PolicyRateLimitRule] = Field(default_factory=list)
    audit: PolicyAuditConfig = Field(default_factory=PolicyAuditConfig)


class StreamingConfig(BaseModel):
    """Streaming progress for long-running tool calls.

    When a client provides a progressToken in the call's `_meta`, the gateway:
      * forwards every progress notification it receives from the backend
        upstream to the client (`passthrough`), and
      * emits a periodic "still working" heartbeat every
        `heartbeat_interval_seconds` so the client knows the call hasn't hung
        even when the backend is silent.

    Setting `heartbeat_interval_seconds: 0` disables heartbeats entirely.
    """

    enabled: bool = True
    passthrough: bool = True
    heartbeat_interval_seconds: float = 5.0


class SessionsConfig(BaseModel):
    """Optional cross-restart session persistence.

    Off by default. When enabled the session manager periodically dumps active
    sessions to a JSON file and loads them at startup. Atomic via write+rename.
    """

    persist: bool = False
    persist_path: str = ".cloud-engineer-mcp/sessions.json"
    persist_interval_seconds: float = 30.0
    ttl_seconds: int = 3600


class CloudEngineerConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    selector: SelectorConfig = Field(default_factory=SelectorConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    backends: dict[str, BackendConfig] = Field(default_factory=dict)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> CloudEngineerConfig:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        data = _deep_interpolate(data)
        return cls.model_validate(data)

    def redacted(self) -> dict[str, object]:
        """Return config dict with sensitive env values redacted."""
        data = self.model_dump()
        for backend in data.get("backends", {}).values():
            env = backend.get("env", {})
            for key in env:
                upper = key.upper()
                if any(s in upper for s in ("SECRET", "KEY", "TOKEN", "PASSWORD", "CREDENTIAL")):
                    env[key] = "***REDACTED***"
        return data
