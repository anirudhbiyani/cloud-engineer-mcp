# Contributing to cloud-engineer-mcp

Thanks for taking the time to contribute! This project is small, easy to
understand, and easy to run locally. The goal of this guide is to keep your
first contribution short.

## Quick checklist

- [ ] You agree your contribution is licensed under [AGPL-3.0-or-later](LICENSE).
- [ ] Tests pass: `pytest`.
- [ ] Lint passes: `ruff check . && ruff format --check .`.
- [ ] You added tests for new behavior.
- [ ] You updated `CHANGELOG.md` under `[Unreleased]`.

## Development setup

```bash
git clone https://github.com/cloud-engineer-mcp/cloud-engineer-mcp.git
cd cloud-engineer-mcp
uv sync --extra dev          # creates .venv and installs dev dependencies
uv run pytest                # 100+ tests, ~10 seconds
uv run ruff check .
```

To exercise the gateway end-to-end without any cloud setup:

```bash
uv run cloud-engineer-mcp demo
```

## Where things live

```
src/cloud_engineer_mcp/
  cli.py                    Click CLI entrypoints
  config.py                 Pydantic config models, YAML loader, env interpolation
  discovery.py              AWS/Azure/GCP auto-discovery + credential validation
  server.py                 MCP server wiring (list_tools, call_tool, set_context)
  backends/
    manager.py              Lifecycle for all backend subprocesses
    process.py              One stdio MCP subprocess wrapper
    registry.py             Namespaced tool catalog
  selector/
    engine.py               sentence-transformer wrapper with LRU query cache
    index.py                vector index over tool descriptions
    context.py              build a search query from session state
  session/sessions.py       per-session state (context, pins, tool history)
  transport/
    stdio.py                stdio transport runner
    http.py                 Streamable HTTP runner with auth + rate limit
  observability/
    logging.py              structlog setup
    metrics.py              in-memory counters/histograms + /metrics endpoint
    health.py               /readyz handler
```

## Testing strategy

- **Unit tests** (`tests/unit/`) cover individual modules with mocked dependencies. Fast.
- **Integration tests** (`tests/integration/`) start real subprocesses against the bundled mock backend (`tests/fixtures/mock_backend.py`) and exercise the full lifecycle. Slower (~8s).
- **HTTP smoke tests** (`tests/integration/test_http_transport.py`) verify the ASGI surface and auth gate without booting MCP sessions.

Add tests for any new public behavior. We aim for >80% line coverage and won't merge changes that drop it materially.

## Commit conventions

- Imperative subject ≤72 chars. Body wrap at 72.
- Conventional Commits welcome but not required (`feat:`, `fix:`, `docs:`).
- One logical change per PR. If you're adding a feature, a separate refactor PR first usually reviews faster.

## Pull request flow

1. Fork the repo. Create a feature branch.
2. Make your change with tests. Run `pytest` and `ruff check .` locally.
3. Push and open a PR against `main`. Fill in the PR template.
4. A maintainer will review within a few days. CI must be green.
5. We squash-merge.

## Good first issues

Look for issues tagged [`good first issue`](https://github.com/cloud-engineer-mcp/cloud-engineer-mcp/labels/good%20first%20issue).
Comment on an issue to claim it before starting.

## Adding a new backend

Most clouds you'd want are already covered. To add a new auto-discovered backend (e.g. Cloudflare, DigitalOcean):

1. Add a `discover_<provider>()` async function to `discovery.py`.
2. Add a `<Provider>DiscoveryConfig` Pydantic model to `config.py`.
3. Add an arm in `expand_backends()` that produces a `BackendConfig`.
4. Add unit tests in `tests/unit/test_discovery.py`.

No change to `BackendProcess` or `BackendManager` should be necessary — they're provider-agnostic.

## Releases

Maintainers cut releases via GitHub Releases UI. The `release.yml` workflow
builds sdist+wheel via `uv build` and publishes to PyPI via Trusted Publisher. The
Docker image is pushed to `ghcr.io/cloud-engineer-mcp/cloud-engineer-mcp`.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
