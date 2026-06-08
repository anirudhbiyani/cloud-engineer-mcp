# Architecture

`cloud-engineer-mcp` is a thin (~2k LOC) async Python gateway that sits between
an MCP client (Cursor, VS Code, Claude Desktop, HTTP) and one or more
**backend** MCP servers (typically the official AWS, Azure, and GCP MCP
servers). It exists to solve one problem: **plugging multiple cloud MCP
servers into an agent floods context with hundreds of tool definitions**.

## Components

```
┌──────────────────────────────────────────────────────────────┐
│  MCP Client (Cursor / VS Code / Claude Desktop / HTTP)        │
└────────────────────────────┬─────────────────────────────────┘
                             │ MCP (stdio or Streamable HTTP)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Server (cloud_engineer_mcp.server)                           │
│   • list_tools()  → ToolIndex.search(...)                     │
│   • call_tool()   → ToolRegistry.lookup → BackendManager.route│
│   • set_context() → Session.set_context                       │
└──┬──────────┬──────────────┬──────────────┬──────────────────┘
   │          │              │              │
   ▼          ▼              ▼              ▼
ToolIndex  ToolRegistry  SessionManager  BackendManager
   │          ▲                            │
   │          │                            ▼
   │          └────── tools/list ◀──── BackendProcess (x N)
   ▼                                       │
EmbeddingEngine                            │ stdio MCP
(sentence-transformer)                     ▼
                                       Official cloud MCP servers
                                       (uvx awslabs.ccapi-mcp-server, etc.)
```

### `BackendProcess`
Wraps a single downstream MCP stdio subprocess. State machine:
`STOPPED → STARTING → READY → (FAILED | STOPPED | RESTARTING)`. Uses
`mcp.client.stdio.stdio_client` under an `AsyncExitStack`. Bounded startup
timeout per backend; bounded number of automatic restarts.

### `BackendManager`
Owns the dict of `BackendProcess` keyed by backend ID. Starts the first
`DEFAULT_EAGER_LIMIT` (10) backends eagerly; the rest start lazily on first
tool call. Runs a per-backend health-check loop that restarts a backend when
`list_tools` stops responding.

### `ToolRegistry`
Catalog of every tool from every backend, keyed by **namespaced name**
(`<backend_id>__<tool_name>`, truncated to 40 chars). One reverse index from
`backend_id → [namespaced_name, ...]` for O(1) unregister and pin operations.

### `EmbeddingEngine`
Loads `all-MiniLM-L6-v2` (22M params, ~80MB). Encodes text to L2-normalized
32-dim float vectors. LRU cache (default 128 entries) on `encode_single` for
hot query reuse. Model load is offloaded to a thread pool so it doesn't block
the event loop.

### `ToolIndex`
Vector index over every tool's `description_for_embedding`. Search is one
matrix-vector multiply (`scores = matrix @ query_vec`), masked by allowed
provider, boosted by session pins, partitioned to top-K. ~5ms p99 for 800
tools. Embeddings persist to a single `.npz` between restarts; cache is
invalidated when the tool name list changes.

### `Session`
Per-conversation state: current context string, recent message and tool-call
history (capped), pinned tools with a per-pin decay counter. `SessionManager`
expires sessions after 1h of inactivity via a background cleanup task.

### `ContextExtractor`
Builds a single search query from `(user_message, conversation_history,
tool_call_history)`. Latest user message gets the most weight; recent tool
names contribute a "momentum bias"; recent assistant messages add a tail of
keywords. Truncated to `context_max_tokens * 4` chars.

### `DiscoveryConfig`
Three independent provider scanners running concurrently when called via
`discover_all`:

- **AWS**: parses `~/.aws/config` (no API call needed for enumeration); one
  account = one backend.
- **Azure**: `az account list --output json` then per-subscription credential
  validation via `az account show`.
- **GCP**: `gcloud projects list --format json` and `gcloud auth
  print-access-token` for credential validation. **One backend total** —
  the official GCP MCP server handles multi-project routing internally.

Invalid-credentials accounts are skipped silently with a warning. If no
provider yields any valid account, the gateway raises `CredentialError` at
startup with provider-specific remediation hints.

### Transports

- **stdio** (`transport/stdio.py`): canonical for IDEs. The MCP server speaks
  newline-delimited JSON-RPC on stdin/stdout; logs go to stderr.
- **Streamable HTTP** (`transport/http.py`): canonical for remote/team
  deployments. ASGI app built on Starlette + `StreamableHTTPSessionManager`,
  with bearer-token auth (`CLOUD_ENGINEER_MCP_AUTH_TOKEN`), per-IP token-bucket
  rate limiting, optional CORS, and `/livez` + `/readyz` + `/metrics`.

## Lifecycle

```
cli.serve
  └─ CloudEngineerConfig.from_yaml
  └─ GatewayComponents.create
        └─ discover_all          (network-bound; bounded with timeouts)
        └─ expand_backends       (discovered + manual → final dict)
  └─ create_server               (wires server.list_tools, call_tool)
  └─ components.startup
        └─ background task: start_all (eager 10) → load model
            → build index (or load cache) → notify_tools_changed
            → start_health_monitors
  └─ run_stdio | run_http        (transport loop)
  └─ components.shutdown         (always; cancels background tasks)
```

`startup()` returns within ~50ms so the MCP stdio transport can begin
accepting requests immediately. Cursor's startup timeout is 30s; we
deliberately don't block on backend startup or model load.

## Performance

- **Per-turn cost**: 1 query embedding (~2ms cached, ~6ms cold) + 1 matvec
  (~1ms for 800 tools) + sort/partition (~0.5ms). End-to-end <10ms p99.
- **Memory footprint**: model ~250MB resident; tool matrix (800×384 float32)
  ~1.2MB; sessions ~5KB each. Run `scripts/benchmark_selector.py` to verify on
  your hardware.
- **Cold start**: ~1.5–3s typical (model load + cache hit) on M-series Macs.
  First-ever start: 10–20s if backends are downloaded via `npx`/`uvx`.

## Observability

| Endpoint | Purpose |
|----------|---------|
| `/livez` | Liveness — always 200 once the process is up |
| `/readyz` | Readiness — at least one backend READY and embedding model loaded |
| `/health` | Backwards-compatible alias for `/readyz` |
| `/metrics` | JSON in-memory counters/histograms |

Structured log fields:

- `gateway.starting` / `gateway.ready` / `gateway.shutting_down`
- `backend.starting` / `backend.ready` / `backend.failed` / `backend.restarting`
- `tool_call.start` / `tool_call.complete` / `tool_call.error`
- `tool_selection.results` with `duration_ms` and top-5 scores
- `discovery.{aws,azure,gcp}.{found,timeout,credentials_invalid}`

All logs go to stderr by default so stdout remains pure JSON-RPC.
