# cloud-engineer-mcp

**Unified Multi-Cloud MCP Gateway Server**

cloud-engineer-mcp is a gateway MCP (Model Context Protocol) server that unifies official AWS, Azure, and GCP MCP servers behind a single interface. It uses embedding-based semantic similarity to automatically surface only the most relevant tools for each conversation, eliminating context bloat from hundreds of cloud tools.

## Features

- **Multi-cloud unification** -- AWS, Azure, and GCP tools behind one MCP endpoint
- **Smart tool selection** -- Embedding-based semantic search returns only relevant tools (top-K)
- **Dual transport** -- stdio (for IDEs like Cursor/VS Code) and Streamable HTTP (for remote deployments)
- **Session-aware** -- Conversation context and tool pinning for consistent workflows
- **Auto-recovery** -- Backend crash detection and automatic restart
- **Zero LLM cost** -- Local sentence-transformer model for tool selection (no API calls)

## Prerequisites

- **Python 3.14+**
- **Node.js 20+** and **npm** -- required for Azure and GCP backends (installed via `npx`)
- **uv** ([astral.sh/uv](https://astral.sh/uv)) -- required for AWS backends (installed via `uvx`)

You only need the tools for the cloud providers you plan to use:

| Provider | Required CLI | Backend runner |
|----------|-------------|----------------|
| AWS | `aws` CLI v2 | `uvx` (from `uv`) |
| Azure | `az` CLI | `npx` (from Node.js) |
| GCP | `gcloud` CLI | `npx` (from Node.js) |

## Quick Start

### Installation

```bash
pip install -e ".[dev]"
```

### Configuration

Copy and edit the example config:

```bash
cp config.example.yml config.yml
# Edit config.yml to enable/disable cloud providers
```

**Disable providers you don't use.** If a provider is enabled but its CLI is not installed or not authenticated, the server startup will be slow or may fail. Set `enabled: false` for any provider you don't need:

```yaml
discovery:
  aws:
    enabled: false   # disable if you don't use AWS
  azure:
    enabled: true
  gcp:
    enabled: false   # disable if you don't use GCP
```

### Cloud Provider Authentication

Before starting the server, authenticate with the cloud providers you plan to use:

**AWS:**
```bash
# SSO-based authentication
aws sso login --profile <your-profile>

# Or configure access keys
aws configure --profile <your-profile>
```

**Azure:**
```bash
az login
```

**GCP:**
```bash
gcloud auth login
gcloud config set project <your-project-id>
```

### Running

```bash
# stdio transport (for IDE integration)
cloud-engineer-mcp serve --transport stdio

# HTTP transport (for remote/API access)
cloud-engineer-mcp serve --transport http

# Both transports
cloud-engineer-mcp serve --transport both
```

### Validate Config

```bash
cloud-engineer-mcp check
```

### Preview Discovered Accounts

```bash
cloud-engineer-mcp discover
```

### List All Tools

```bash
cloud-engineer-mcp list-tools
```

### Pre-install Backends (Optional)

For faster startup, pre-install the backend MCP server packages:

```bash
cloud-engineer-mcp install-backends
```

## IDE Integration

### Cursor / VS Code

Run the auto-installer:

```bash
cloud-engineer-mcp cursor-install --config /path/to/config.yml
```

Or manually add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "cloud-engineer-mcp": {
      "command": "python",
      "args": ["-m", "cloud_engineer_mcp", "serve", "--config", "/path/to/config.yml", "--transport", "stdio"]
    }
  }
}
```

### Remote HTTP

```json
{
  "mcpServers": {
    "cloud-engineer-mcp": {
      "url": "http://localhost:8080/mcp",
      "transport": "streamable-http"
    }
  }
}
```

> **Security warning:** The HTTP transport has no built-in authentication. By default it binds to `127.0.0.1` (localhost only). If you need remote access, place it behind a reverse proxy with authentication. Never expose the HTTP endpoint directly to the public internet.

## How Tool Selection Works

1. The AI agent calls `set_context("I need to deploy an S3 bucket with versioning")`
2. cloud-engineer-mcp encodes this context using a local sentence-transformer model
3. On the next `tools/list`, cloud-engineer-mcp computes cosine similarity between the context and all tool descriptions
4. Only the top-K most relevant tools are returned (default: 15)
5. When a tool is called, tools from the same backend get a score boost ("pinning") so related tools stay available

## Architecture

```
MCP Clients (Cursor, VS Code, HTTP)
        │
        ▼
┌─────────────────────────────┐
│  cloud-engineer-mcp Gateway │
│                             │
│  Tool Selector (embeddings) │
│  Tool Registry (namespaced) │
│  Session Manager            │
│  Backend Manager            │
└──┬──────────┬──────────┬────┘
   │          │          │
   ▼          ▼          ▼
AWS MCP    Azure MCP   GCP MCP
(subprocess) (subprocess) (subprocess)
```

### How Discovery Works

On startup, cloud-engineer-mcp scans your machine for configured cloud accounts:

- **AWS**: Parses `~/.aws/config` to find all configured profiles
- **Azure**: Runs `az account list` to find enabled subscriptions
- **GCP**: Runs `gcloud projects list` to find active projects

Each discovered account becomes a backend MCP server subprocess. AWS creates one backend per profile, Azure creates one per subscription, and GCP creates a single backend that covers all projects (the GCP MCP server handles multi-project routing internally).

### GCP Note

GCP uses a single backend for all discovered projects. The default project is set from the first discovered project. To work with a specific project, use `gcloud config set project <id>` before starting the server, or use `set_context` to scope the conversation.

## Environment Variables

All CLI options can also be set via environment variables:

| Variable | CLI Option | Description |
|----------|-----------|-------------|
| `CLOUD_ENGINEER_MCP_CONFIG` | `--config` | Path to config file |
| `LOG_LEVEL` | `--log-level` | Log level (DEBUG, INFO, WARNING, ERROR) |

## Docker

```bash
docker compose up -d
```

The Docker setup mounts your local cloud credentials read-only:

- `~/.aws` for AWS
- `~/.azure` for Azure
- `~/.config/gcloud` for GCP

> **Security warning:** The Docker image binds to `0.0.0.0` inside the container to allow port mapping. If you publish the port, ensure the host firewall or a reverse proxy restricts access.

## Testing

```bash
# Unit tests
pytest tests/unit/ -v

# Integration tests (starts mock backends)
pytest tests/integration/ -v --timeout=60

# All tests with coverage
pytest --cov=cloud_engineer_mcp --cov-report=html
```

## Configuration Reference

See `config.example.yml` for all available options. Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `selector.model_name` | `all-MiniLM-L6-v2` | Sentence-transformer model for embeddings |
| `selector.top_k` | `15` | Max tools returned per `tools/list` |
| `selector.min_similarity` | `0.15` | Floor threshold for tool similarity |
| `selector.cache_embeddings` | `true` | Cache embeddings to disk for faster restarts |
| `discovery.enabled` | `true` | Enable auto-discovery of cloud accounts |
| `server.transports.http.host` | `127.0.0.1` | HTTP bind address |
| `server.transports.http.port` | `8080` | HTTP port |

## Troubleshooting

**"CLI not found" for a provider**
Install the missing CLI tool (`aws`, `az`, or `gcloud`), or set `enabled: false` for that provider in `config.yml`.

**"Credentials invalid" on startup**
Run the appropriate login command for your provider (see [Cloud Provider Authentication](#cloud-provider-authentication) above).

**Startup is slow**
Backend MCP servers are downloaded on first use via `uvx`/`npx`. Run `cloud-engineer-mcp install-backends` to pre-install them for instant startup.

**"API not enabled" errors (GCP)**
The GCP MCP server may require specific APIs to be enabled in your project. Enable them via the Google Cloud Console or `gcloud services enable <api>`.

**Too many/few tools returned**
Adjust `selector.top_k` in your config. Use `set_context` with `cloud_providers` to filter by provider.

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes with tests
4. Run the test suite (`pytest`)
5. Run the linter (`ruff check . && ruff format --check .`)
6. Submit a pull request