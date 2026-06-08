# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Auto-refreshing tokens for remote backends.** New `auth_refresh_command` / `auth_header_name` / `auth_header_template` fields on `BackendConfig` install an `httpx.Auth` flow that mints a Bearer token from a shell command and re-mints on 401. GCP managed-MCP backends use this by default, so short-lived `gcloud auth print-access-token` tokens rotate without restarting the backend.
- **AWS managed-MCP proxy** (`aws_managed`): single remote backend pointing at `https://aws-mcp.us-east-1.api.aws/mcp`. Anonymous today; opt in via `discovery.aws_managed.enabled: true`.
- **Eval catalog & dataset expanded** to cover the new backend types: Kubernetes (10 tools), Cloudflare (6), DigitalOcean (4), Azure DevOps (5), Playwright/GitHub-remote/Microsoft-Learn/AWS-Knowledge/AWS-managed/GCP-managed (~14). 25 new labeled eval cases. Total: 114-tool catalog, 72 cases. Embedding backend still hits 100% Recall@15; BM25 hits 97.2%.
- **Remote MCP transport.** Backends can now connect over HTTPS to a streamable-HTTP MCP server, not just stdio subprocesses. `BackendConfig.transport: stdio | http`. Header values support `${ENV_VAR}` interpolation so tokens stay out of files.
- **Built-in remote MCP integrations**: GitHub MCP (token-gated), Microsoft Learn MCP (anonymous), AWS Knowledge MCP (anonymous), Google Cloud managed-MCP services (`gcloud_remote.services` list with `gcloud auth print-access-token` minted at startup; ~40 endpoints addressable).
- **Microsoft ecosystem extras**: Azure DevOps MCP (per-organization stdio backend) and Playwright MCP (single stdio backend) added as discoverable providers.
- **Multiple AWS Labs servers per profile**: `discovery.aws.extra_servers: ["awslabs.lambda-tool-mcp-server@latest", ...]` spawns one specialized subprocess per (profile × extra) alongside the primary IaC server. New `docs/AWS-EXTRAS.md` catalogs the ~30 official AWS Labs servers.
- `docs/REMOTE-MCP.md` — when to use http vs. stdio, GCP token rotation, manual remote-backend examples.
- `cloud-engineer-mcp demo` subcommand: boots the gateway with bundled mock backends so first-time users can try it without any cloud setup.
- `cloud-engineer-mcp eval` subcommand: runs a labeled Recall@K evaluation against a synthetic ~80-tool catalog. Use as a CI gate via `--threshold`. Bundled embedding eval hits 100% Recall@15; BM25 hits 97.9%.
- `cloud-engineer-mcp plugins` subcommand: lists installed third-party backend plugins discovered via entry points.
- **Three new built-in backends**: Kubernetes (per kubeconfig context), Cloudflare (single, env-token), and DigitalOcean (single, env-token). Off by default; flip `discovery.<provider>.enabled: true` in config.
- **Pluggable selector backends**: choose between the existing `embedding` selector and a new pure-Python `bm25` backend via `selector.backend`. BM25 needs no model download and ranks in ≤1ms on small catalogs.
- **Plugin SDK**: third-party packages can register a `BackendProvider` against the `cloud_engineer_mcp.backend_providers` entry-point group to add any MCP-shaped backend without forking. Reference plugin in `examples/plugin-flyio/`; authoring guide in `docs/PLUGINS.md`.
- **Policy engine**: per-tool allow/deny (fnmatch globs), per-tool rate limiting (sliding window), dry-run mode (no backend invocation, returns a stub), and append-only JSONL audit log. All off by default. Configured under the top-level `policy` block.
- **Streaming progress**: when an MCP client provides a `progressToken`, the gateway forwards backend progress notifications upstream and emits "still working" heartbeats every `streaming.heartbeat_interval_seconds`. Backend → client passthrough is opt-in via `streaming.passthrough`.
- `--version`, `-v/--verbose`, `-q/--quiet` flags on the CLI. `-v`=INFO, `-vv`=DEBUG, `-q`=ERROR. Flag wins over `LOG_LEVEL` env var, which wins over config.
- HTTP transport bearer-token authentication via `CLOUD_ENGINEER_MCP_AUTH_TOKEN`. The transport refuses to start when bound to a non-loopback host without a token.
- `/livez` (liveness) split from `/readyz` (readiness) for Kubernetes-style probes.
- Prometheus text format on `/metrics` via `Accept: text/plain` (or `application/openmetrics-text`) content negotiation. JSON remains the default.
- SIGTERM / SIGINT handling in `cloud-engineer-mcp serve` — clean shutdown under Docker, systemd, Kubernetes.
- Exponential backoff with jitter between backend restart attempts. Configurable per backend via `restart_backoff_base_seconds` / `restart_backoff_max_seconds`.
- Public `ToolIndex.is_loaded` property; `/readyz` no longer reaches into selector internals.
- Embedding cache `version` field so future embed-text format changes invalidate stale caches automatically.
- `set_context` accepts optional `action` and `resource_type` structured intent fields. When present they up-weight the embedding query for higher selection precision.
- Optional cross-restart session persistence (atomic JSON dump). Enable via `sessions.persist: true`. Default off.
- Optional OpenTelemetry tracing. Install the extra from source via `uv sync --extra otel`. Auto-enables when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Emits spans for `tool_selection`, `tool_call`, and `backend.start`. No-op cleanly when the SDK isn't installed.
- `examples/` directory with Cursor and Claude Desktop configuration templates.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `docs/FAQ.md`, `docs/DEMO.md`.
- GitHub Actions CI workflow (ruff + pytest matrix across Python 3.12 / 3.13 / 3.14).
- GitHub Actions release workflow: PyPI Trusted Publisher + multi-arch Docker image to `ghcr.io`.
- GitHub issue and pull-request templates.
- Integration smoke tests for the HTTP transport. Unit tests for metrics, backoff, and cache versioning.

### Changed
- **AWS default updated**: `discovery.aws.mcp_server` now defaults to `awslabs.aws-iac-mcp-server@latest`. AWS Labs has deprecated the previous default (`awslabs.ccapi-mcp-server`). The new server is a superset.
- **Kubernetes default updated**: `discovery.kubernetes.mcp_command` now defaults to the Red Hat-backed Go-native `npx -y kubernetes-mcp-server@latest` (`containers/kubernetes-mcp-server`). Single binary, no kubectl/Node/Python deps, native OpenShift support. Override to use Flux159 instead.
- **BREAKING**: HTTP transport rewritten against MCP SDK 1.27+ `StreamableHTTPSessionManager` + Starlette `lifespan`. Previous releases targeted an obsolete API; HTTP did not work on any current MCP SDK.
- **BREAKING**: Python floor raised from "3.14" (declared) / "3.12" (linted) to a consistent `>=3.12` across `pyproject.toml`, `Dockerfile`, ruff, and mypy.
- **BREAKING**: `cors_origins` default changed from `["*"]` to `[]`. Explicit opt-in required.
- License declaration in `pyproject.toml` corrected from `MIT` to `AGPL-3.0-or-later`, matching the `LICENSE` file.
- README rewritten to lead with the problem statement and a 60-second try-it path.
- mypy --strict now clean across the package (34 → 0 errors).

### Fixed
- `BackendProcess._cleanup_stack` no longer suppresses `KeyboardInterrupt` / `SystemExit`. Narrow to `Exception` and `CancelledError`.
- Demo subcommand bypasses cloud discovery — no spurious credential errors on a fresh machine.
- Variable-name collision in `ToolIndex._vector_search` that broke type-checking and confused readers (`idx` was used for both provider and tool indices).

### Security
- HTTP transport gains a bearer-token gate. The gateway refuses to listen off-loopback without a token.
- `cors_origins` default tightened.

## [1.0.0] - 2025-04-05

Initial public beta. Stdio transport, semantic tool selection, AWS/Azure/GCP
auto-discovery, session-aware pinning, embedding cache, Cursor auto-install.
