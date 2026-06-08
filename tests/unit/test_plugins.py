"""Tests for the third-party backend plugin SDK."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cloud_engineer_mcp.config import BackendConfig
from cloud_engineer_mcp.discovery import DiscoveredAccount
from cloud_engineer_mcp.plugins import (
    BackendProvider,
    LoadedPlugin,
    discover_from_plugins,
    expand_from_plugins,
    iter_loaded_plugins,
)


class _GoodProvider:
    """Reference plugin used across tests."""

    name = "demo"

    async def discover(self) -> list[DiscoveredAccount]:
        return [
            DiscoveredAccount(
                provider="demo",
                profile_id="alpha",
                display_name="Demo (alpha)",
                env_vars={"DEMO_TOKEN": "abc"},
                credentials_valid=True,
            )
        ]

    def expand(self, account: DiscoveredAccount) -> BackendConfig:
        return BackendConfig(
            display_name=account.display_name,
            command="echo",
            args=["hello"],
            env=account.env_vars,
        )


class _BrokenProvider:
    name = "broken"

    async def discover(self) -> list[DiscoveredAccount]:
        raise RuntimeError("simulated failure")

    def expand(self, account: DiscoveredAccount) -> BackendConfig:
        raise RuntimeError("unreachable")


def _entry_point(name: str, cls: type) -> MagicMock:
    """Build a MagicMock that looks like an importlib.metadata EntryPoint."""
    ep = MagicMock()
    ep.name = name
    ep.value = f"<test>:{cls.__name__}"
    ep.load.return_value = cls
    ep.dist = MagicMock(name="dist", version="0.0.1")
    ep.dist.name = "demo-pkg"
    ep.dist.version = "0.0.1"
    return ep


class TestProtocolRecognition:
    def test_good_provider_is_a_backend_provider(self) -> None:
        assert isinstance(_GoodProvider(), BackendProvider)

    def test_missing_methods_not_backend_provider(self) -> None:
        class BadShape:
            name = "bad"

        assert not isinstance(BadShape(), BackendProvider)


class TestIterLoadedPlugins:
    def test_loads_well_formed_plugin(self) -> None:
        ep = _entry_point("demo", _GoodProvider)
        with patch("cloud_engineer_mcp.plugins.entry_points", return_value=[ep]):
            plugins = list(iter_loaded_plugins())
        assert len(plugins) == 1
        assert isinstance(plugins[0], LoadedPlugin)
        assert plugins[0].name == "demo"
        assert plugins[0].distribution == "demo-pkg"

    def test_skips_plugin_that_fails_to_import(self) -> None:
        ep = MagicMock()
        ep.name = "broken_import"
        ep.value = "missing:Symbol"
        ep.load.side_effect = ImportError("nope")
        with patch("cloud_engineer_mcp.plugins.entry_points", return_value=[ep]):
            plugins = list(iter_loaded_plugins())
        assert plugins == []

    def test_skips_plugin_that_doesnt_satisfy_protocol(self) -> None:
        class NotAProvider:
            pass

        ep = _entry_point("fake", NotAProvider)
        with patch("cloud_engineer_mcp.plugins.entry_points", return_value=[ep]):
            plugins = list(iter_loaded_plugins())
        assert plugins == []


@pytest.mark.asyncio
class TestDiscoverFromPlugins:
    async def test_returns_accounts(self) -> None:
        ep = _entry_point("demo", _GoodProvider)
        with patch("cloud_engineer_mcp.plugins.entry_points", return_value=[ep]):
            accounts = await discover_from_plugins()
        assert len(accounts) == 1
        assert accounts[0].provider == "demo"

    async def test_broken_plugin_does_not_break_others(self) -> None:
        good = _entry_point("demo", _GoodProvider)
        broken = _entry_point("broken", _BrokenProvider)
        with patch("cloud_engineer_mcp.plugins.entry_points", return_value=[good, broken]):
            accounts = await discover_from_plugins()
        # The good plugin yields 1 account; the broken one logs and is skipped.
        assert len(accounts) == 1
        assert accounts[0].provider == "demo"


class TestExpandFromPlugins:
    def test_expands_known_provider(self) -> None:
        ep = _entry_point("demo", _GoodProvider)
        account = DiscoveredAccount(
            provider="demo",
            profile_id="alpha",
            display_name="Demo (alpha)",
            env_vars={"DEMO_TOKEN": "abc"},
        )
        with patch("cloud_engineer_mcp.plugins.entry_points", return_value=[ep]):
            backends = expand_from_plugins([account])
        assert len(backends) == 1
        bid = next(iter(backends))
        assert bid.startswith("plugin_demo_")
        assert backends[bid].command == "echo"

    def test_unknown_provider_skipped(self) -> None:
        ep = _entry_point("demo", _GoodProvider)
        account = DiscoveredAccount(
            provider="not_demo",
            profile_id="x",
            display_name="X",
        )
        with patch("cloud_engineer_mcp.plugins.entry_points", return_value=[ep]):
            backends = expand_from_plugins([account])
        assert backends == {}

    def test_invalid_credentials_skipped(self) -> None:
        ep = _entry_point("demo", _GoodProvider)
        account = DiscoveredAccount(
            provider="demo",
            profile_id="alpha",
            display_name="Demo",
            credentials_valid=False,
        )
        with patch("cloud_engineer_mcp.plugins.entry_points", return_value=[ep]):
            backends = expand_from_plugins([account])
        assert backends == {}
