"""Plugin SDK for third-party backend providers.

External Python packages can ship MCP backends for clouds the gateway
doesn't natively support — Fly.io, Hetzner, Vercel, your internal MCP
servers, etc. — without forking this repo.

Registration
------------
A plugin package declares an entry point in `pyproject.toml`:

    [project.entry-points."cloud_engineer_mcp.backend_providers"]
    fly = "my_fly_plugin:FlyBackendProvider"

The named object must satisfy the `BackendProvider` protocol below. At
gateway startup, every installed entry point is loaded, its `discover()` is
called, and the resulting `DiscoveredAccount`s flow through the same pipeline
as the built-in AWS/Azure/GCP providers.

Authoring
---------
The simplest plugin is one class with two methods:

    class FlyBackendProvider:
        name = "fly"
        async def discover(self) -> list[DiscoveredAccount]:
            ...
        def expand(self, account: DiscoveredAccount) -> BackendConfig:
            ...

A complete reference plugin lives at `examples/plugin-flyio/`.

Trust model
-----------
Plugins run **in-process** with the gateway and inherit its credentials.
Install only plugins you trust. The gateway does no sandboxing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from cloud_engineer_mcp.observability.logging import get_logger

if TYPE_CHECKING:
    from cloud_engineer_mcp.config import BackendConfig
    from cloud_engineer_mcp.discovery import DiscoveredAccount

log = get_logger("plugins")

ENTRY_POINT_GROUP = "cloud_engineer_mcp.backend_providers"


@runtime_checkable
class BackendProvider(Protocol):
    """Interface a third-party plugin must implement.

    Two responsibilities:

    1. ``discover()`` — return zero or more DiscoveredAccount instances
       representing whatever this plugin manages (a profile, a subscription,
       a project, a single API key, etc.). Network calls are fine; keep
       them under a reasonable timeout.
    2. ``expand()`` — turn one DiscoveredAccount into a BackendConfig that
       the gateway will use to launch the underlying MCP subprocess.
    """

    name: str

    async def discover(self) -> list[DiscoveredAccount]: ...

    def expand(self, account: DiscoveredAccount) -> BackendConfig: ...


@dataclass(frozen=True)
class LoadedPlugin:
    """A plugin we successfully loaded from an entry point."""

    name: str
    provider: BackendProvider
    distribution: str
    version: str


def iter_loaded_plugins() -> Iterable[LoadedPlugin]:
    """Load and yield every plugin registered under our entry-point group.

    A plugin that fails to import is logged and skipped — one broken plugin
    must never prevent the gateway from starting.
    """
    eps = entry_points(group=ENTRY_POINT_GROUP)

    for ep in eps:
        try:
            cls = ep.load()
            instance = cls() if callable(cls) and not isinstance(cls, type) else cls()
        except Exception as exc:
            log.error(
                "plugins.load_failed",
                entry_point=ep.name,
                value=ep.value,
                error=str(exc),
            )
            continue

        if not isinstance(instance, BackendProvider):
            log.error(
                "plugins.protocol_mismatch",
                entry_point=ep.name,
                missing="name/discover/expand",
            )
            continue

        dist = getattr(ep, "dist", None)
        dist_name = getattr(dist, "name", "<unknown>")
        dist_version = getattr(dist, "version", "<unknown>")
        log.info(
            "plugins.loaded",
            name=ep.name,
            dist=dist_name,
            version=dist_version,
        )
        yield LoadedPlugin(
            name=ep.name,
            provider=instance,
            distribution=dist_name,
            version=dist_version,
        )


async def discover_from_plugins() -> list[DiscoveredAccount]:
    """Run discover() on every loaded plugin and merge results.

    Plugin discover() errors are logged and the plugin is skipped.
    """
    discovered: list[DiscoveredAccount] = []
    for plugin in iter_loaded_plugins():
        try:
            accounts = await plugin.provider.discover()
        except Exception as exc:
            log.warning(
                "plugins.discover_failed",
                plugin=plugin.name,
                error=str(exc),
            )
            continue
        discovered.extend(accounts)
        log.info(
            "plugins.discovered",
            plugin=plugin.name,
            count=len(accounts),
        )
    return discovered


def expand_from_plugins(
    accounts: list[DiscoveredAccount],
) -> dict[str, BackendConfig]:
    """Convert plugin-sourced DiscoveredAccount instances into BackendConfig.

    Looks up the matching plugin by `account.provider` and calls its expand().
    Backends are keyed as `plugin_<provider>_<safe_profile_id>`.
    """
    if not accounts:
        return {}

    from cloud_engineer_mcp.discovery import _safe_id

    providers: dict[str, BackendProvider] = {
        p.provider.name: p.provider for p in iter_loaded_plugins()
    }
    backends: dict[str, BackendConfig] = {}
    for account in accounts:
        if not account.credentials_valid:
            continue
        provider = providers.get(account.provider)
        if provider is None:
            log.warning(
                "plugins.no_provider_for_account",
                provider=account.provider,
                profile_id=account.profile_id,
            )
            continue
        try:
            backend = provider.expand(account)
        except Exception as exc:
            log.warning(
                "plugins.expand_failed",
                provider=account.provider,
                error=str(exc),
            )
            continue
        provider_id = _safe_id(account.provider, max_len=10)
        profile_id = _safe_id(account.profile_id, max_len=10)
        backends[f"plugin_{provider_id}_{profile_id}"] = backend
    return backends
