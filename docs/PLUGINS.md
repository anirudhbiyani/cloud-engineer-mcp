# Authoring a backend plugin

`cloud-engineer-mcp` ships with first-class support for AWS, Azure, GCP,
Kubernetes, Cloudflare, and DigitalOcean. Anything else — Fly.io, Hetzner,
Vercel, your company's internal MCP servers — is a few lines of Python in a
separate package.

## What you'll write

A class with two methods and a name:

```python
from cloud_engineer_mcp.config import BackendConfig
from cloud_engineer_mcp.discovery import DiscoveredAccount


class FlyBackendProvider:
    name = "fly"

    async def discover(self) -> list[DiscoveredAccount]:
        # Inspect ~/.config/fly, env vars, or call the Fly API.
        # Return zero or more DiscoveredAccount instances.
        ...

    def expand(self, account: DiscoveredAccount) -> BackendConfig:
        # Convert one DiscoveredAccount into the BackendConfig the gateway
        # will use to launch the underlying MCP subprocess.
        return BackendConfig(
            display_name=account.display_name,
            command="npx",
            args=["-y", "@fly/mcp-server@latest"],
            env=account.env_vars,
        )
```

That's it. The protocol is documented in `cloud_engineer_mcp.plugins.BackendProvider`.

## Registering the plugin

Add an entry point to your plugin package's `pyproject.toml`:

```toml
[project.entry-points."cloud_engineer_mcp.backend_providers"]
fly = "my_fly_plugin:FlyBackendProvider"
```

The key (`fly`) becomes the visible plugin name in `cloud-engineer-mcp plugins`.
The value is `<module>:<attribute>` where the attribute is your provider class.

After your plugin is installed into the same environment as
`cloud-engineer-mcp` (`uv pip install my-fly-plugin`), it's picked up at gateway
startup — no config changes required.

## Verifying

```bash
uv pip install my-fly-plugin
uv run cloud-engineer-mcp plugins         # lists your plugin
uv run cloud-engineer-mcp discover        # shows your DiscoveredAccount entries
uv run cloud-engineer-mcp list-tools      # shows the tools your backend exposes
```

## Worked example

A complete, installable reference plugin lives at
[`examples/plugin-flyio/`](../examples/plugin-flyio/) — copy it and edit.

## Design rules

- **Plugins run in-process** with the gateway. They inherit cloud credentials
  the gateway has. Install only plugins you trust.
- **Network calls in `discover()` are fine** but keep them under ~5 seconds.
  Slow discovery delays gateway startup.
- **`expand()` must be cheap and synchronous**. No I/O. Build a BackendConfig
  from data already in the DiscoveredAccount.
- **One plugin can produce many accounts.** AWS-style multi-profile, GCP-style
  multi-project — both work. Each becomes its own subprocess.
- **Failing plugins don't fail the gateway.** An exception in your `discover()`
  is logged and skipped; other plugins and built-ins still load.

## Stability

The `BackendProvider` protocol and the `cloud_engineer_mcp.backend_providers`
entry-point group are part of the project's public API. We'll bump the major
version if either changes incompatibly. See [CHANGELOG.md](../CHANGELOG.md).
