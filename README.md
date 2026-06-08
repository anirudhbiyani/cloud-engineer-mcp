<div align="center">

# cloud-engineer-mcp

**One MCP endpoint for AWS, Azure, and GCP — without context bloat.**

[![CI](https://github.com/cloud-engineer-mcp/cloud-engineer-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/cloud-engineer-mcp/cloud-engineer-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-7c3aed.svg)](https://modelcontextprotocol.io)

</div>

`cloud-engineer-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) gateway. It fans your agent's
requests out to the official AWS, Azure, and Google Cloud MCP servers, then uses a local sentence-transformer
to return only the handful of tools relevant to the current task — typically 15 out of 600–900.

> Stop drowning your agent in cloud tools. Surface the 15 it actually needs.

## The problem

Plug the official AWS, Azure, and GCP MCP servers into Cursor or Claude Desktop and your agent sees
~800 tool definitions every turn. That's 10–15K tokens of context burned before the user has typed anything,
worse tool-selection accuracy, and noticeable latency on every `tools/list` call.

## What this does

- **Auto-discovers** every AWS profile in `~/.aws/config`, every Azure subscription via `az account list`, and every GCP project via `gcloud projects list`.
- **Starts one subprocess per account** against the official cloud MCP servers (`awslabs.ccapi-mcp-server`, `@azure/mcp`, `@google-cloud/gcloud-mcp`).
- **Indexes every tool description** with a local `all-MiniLM-L6-v2` model (22M params, ~80MB on disk, ~5ms per query, no API calls).
- **Returns the top-K tools** for the current conversation via a `set_context` tool. Pin recently-used backends so workflows stay coherent.
- **Speaks both transports**: stdio for IDEs (Cursor, VS Code, Claude Desktop) and Streamable HTTP for remote/team deployments.

<!-- demo.gif -->

## Try it in 60 seconds (no cloud credentials needed)

```bash
git clone https://github.com/cloud-engineer-mcp/cloud-engineer-mcp.git
cd cloud-engineer-mcp
uv sync
uv run cloud-engineer-mcp demo
```

The `demo` subcommand boots a self-contained gateway against bundled mock backends. No AWS/Azure/GCP setup
required. It's the same code path the real gateway uses — useful for evaluating the project, integrating into
CI, or rehearsing a conference demo.

## Use it for real

Authenticate any cloud CLIs you'd like the gateway to discover (you only need the ones you use):

```bash
aws sso login --profile <profile>      # or aws configure
az login
gcloud auth login && gcloud config set project <project>
```

Then install (see [Installation](#installation) below) and register with your IDE:

```bash
uv run cloud-engineer-mcp install-backends     # pre-download AWS/Azure/GCP MCP packages (optional but recommended)
uv run cloud-engineer-mcp cursor-install       # or claude-desktop-install
```

Restart Cursor. Ask it _"deploy an S3 bucket with versioning"_ and watch `tools/list` surface only the relevant
S3 tools from your AWS profile — even though the gateway is indexing tools across all three clouds.

## How tool selection works

1. The agent calls `set_context("I need to deploy an S3 bucket with versioning")`.
2. The gateway encodes the context with the local sentence-transformer.
3. On the next `tools/list` it computes cosine similarity against every backend tool description and returns the top-K.
4. When the agent calls a tool from backend B, every tool in B gets a score boost (a "pin") that decays over the next few turns — so workflows that need 3–4 related tools stay coherent.
5. Embeddings are cached to disk between restarts (`.cloud-engineer-mcp/embeddings_cache.npz`).

No LLM calls. No re-indexing. ~5ms p99 per selection.

## Architecture

```
   MCP Clients (Cursor, VS Code, Claude Desktop, HTTP)
                            │
                            ▼
   ┌─────────────────────────────────────────────────┐
   │  cloud-engineer-mcp gateway                     │
   │   ├─ Tool selector  (local embeddings)          │
   │   ├─ Tool registry  (namespaced: aws__create)   │
   │   ├─ Session state  (context + pinning)         │
   │   └─ Backend manager (subprocess lifecycle)     │
   └─────────────────────────────────────────────────┘
       │              │              │           │
       ▼              ▼              ▼           ▼
   AWS MCP        Azure MCP      GCP MCP    your backends
   (per profile)  (per sub)      (per proj) (config.yml)
```

More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Stability

`cloud-engineer-mcp` is **beta**. The stdio transport is production-grade and stable. The HTTP transport, demo
subcommand, and metrics format may change in 1.x. Selector behavior (top-K, pinning) is tunable but the public
interface (`set_context`, namespaced tool names) is stable. See [CHANGELOG.md](CHANGELOG.md) for breaking changes.

## Installation

> **Note:** `cloud-engineer-mcp` is not yet published to PyPI. Install from source as shown below.

### From source

Install [`uv`](https://docs.astral.sh/uv/) if you don't have it, then:

```bash
git clone https://github.com/cloud-engineer-mcp/cloud-engineer-mcp.git
cd cloud-engineer-mcp
uv sync                   # add --extra dev if you plan to contribute
```

`uv sync` creates a managed virtualenv in `.venv` and installs the project. Prefix commands with
`uv run` (e.g. `uv run cloud-engineer-mcp demo`) or activate the venv with `source .venv/bin/activate`.

### Prerequisites

- Python **3.12+**
- [`uv`](https://docs.astral.sh/uv/) — used to install and run the gateway (and provides `uvx` for AWS backends)
- For AWS backends: the `aws` CLI v2
- For Azure backends: Node.js 20+ and the `az` CLI
- For GCP backends: Node.js 20+ and the `gcloud` CLI

You only need the tools for clouds you plan to use. The gateway gracefully skips providers whose CLI is missing.

## Configuration

Copy the example and adjust:

```bash
cp config.example.yml config.yml
$EDITOR config.yml
```

Key settings:

| Setting | Default | Description |
|---|---|---|
| `selector.top_k` | `15` | Max tools returned per `tools/list` |
| `selector.model_name` | `all-MiniLM-L6-v2` | Sentence-transformer model |
| `selector.min_similarity` | `0.15` | Floor cosine similarity for inclusion |
| `selector.cache_embeddings` | `true` | Persist embeddings between restarts |
| `discovery.{aws,azure,gcp}.enabled` | `true` | Per-provider auto-discovery |
| `server.transports.http.host` | `127.0.0.1` | HTTP bind address (**leave loopback by default**) |
| `server.transports.http.port` | `8080` | HTTP port |
| `rate_limit.requests_per_minute` | `100` | Per-IP token bucket |

See [`config.example.yml`](config.example.yml) and [docs/FAQ.md](docs/FAQ.md) for the full reference.

## CLI

Prefix each command with `uv run` (shown below), or activate the venv (`source .venv/bin/activate`) and drop the prefix.

```
uv run cloud-engineer-mcp demo                 # mock backends, no cloud setup
uv run cloud-engineer-mcp serve --transport stdio
uv run cloud-engineer-mcp serve --transport http
uv run cloud-engineer-mcp serve --transport both
uv run cloud-engineer-mcp check                # validate config
uv run cloud-engineer-mcp discover             # preview auto-discovered accounts
uv run cloud-engineer-mcp list-tools           # list every tool exposed
uv run cloud-engineer-mcp install-backends     # pre-download AWS/Azure/GCP MCP packages
uv run cloud-engineer-mcp cursor-install       # register in .cursor/mcp.json
```

## IDE integration

### Cursor / VS Code

```bash
uv run cloud-engineer-mcp cursor-install
```

Or manually drop into `.cursor/mcp.json` (template: [`examples/cursor-config.json`](examples/cursor-config.json)):

```json
{
  "mcpServers": {
    "cloud-engineer-mcp": {
      "command": "cloud-engineer-mcp",
      "args": ["serve", "--config", "/abs/path/to/config.yml", "--transport", "stdio"]
    }
  }
}
```

### Claude Desktop

See [`examples/claude-desktop-config.json`](examples/claude-desktop-config.json).

### Remote HTTP

```json
{
  "mcpServers": {
    "cloud-engineer-mcp": {
      "url": "https://your-gateway.example.com/mcp",
      "transport": "streamable-http",
      "headers": { "Authorization": "Bearer <your-token>" }
    }
  }
}
```

⚠ **Always set `CLOUD_ENGINEER_MCP_AUTH_TOKEN` and put the gateway behind TLS** when exposing HTTP off
localhost. The gateway holds delegated cloud credentials; treat it like the keys to your cloud account because
that's effectively what it is. See [SECURITY.md](SECURITY.md).

## Docker

```bash
docker compose up -d
```

The compose file mounts `~/.aws`, `~/.azure`, and `~/.config/gcloud` **read-only**. The container binds to
`127.0.0.1` by default; export with explicit auth and TLS.

## Observability

- `/livez` — process up (always 200).
- `/readyz` — at least one backend READY and embedding model loaded.
- `/metrics` — JSON or Prometheus text format via `Accept` header.
- Structured JSON logs to stderr (set `logging.format: console` for dev).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for log fields and metric names.

## Why AGPL-3.0?

`cloud-engineer-mcp` is licensed under [AGPL-3.0-or-later](LICENSE). If you deploy it as a network service,
the network-use clause applies: improvements and modifications you ship should be made available under
the same license. We chose AGPL deliberately so the project remains a healthy open commons and forks
benefit everyone. Internal use, agent integration, and use behind an authenticated boundary are all fine.
If AGPL doesn't fit your needs, get in touch via [discussions](https://github.com/cloud-engineer-mcp/cloud-engineer-mcp/discussions).

## Contributing

We welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md). Good first issues are tagged
[`good first issue`](https://github.com/cloud-engineer-mcp/cloud-engineer-mcp/labels/good%20first%20issue).

## Security

Report vulnerabilities privately per [SECURITY.md](SECURITY.md). Please do not open public issues for
security-sensitive problems.

## License

[AGPL-3.0-or-later](LICENSE) © cloud-engineer-mcp contributors.
