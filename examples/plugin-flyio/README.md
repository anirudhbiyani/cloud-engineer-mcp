# cloud-engineer-mcp Fly.io plugin (reference example)

This is a runnable reference for the [plugin SDK](../../docs/PLUGINS.md).

It's intentionally minimal:

- One file (`src/cloud_engineer_mcp_flyio/__init__.py`).
- One env var to authenticate (`FLY_API_TOKEN`).
- One MCP subprocess.

## Try it

```bash
cd examples/plugin-flyio
uv pip install -e .                 # into the same venv as cloud-engineer-mcp
export FLY_API_TOKEN=fo1_xxx
uv run cloud-engineer-mcp plugins
uv run cloud-engineer-mcp discover
```

## Copy it for your own backend

Replace `fly` / `Fly` / `FLY_API_TOKEN` with your provider's name, the entry
point name in `pyproject.toml`, and the MCP server package in `expand()`.
The whole plugin is ~30 lines.
