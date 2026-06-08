"""Tests for the remote-MCP backend transport and discovery integrations."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from cloud_engineer_mcp.config import (
    BackendConfig,
    DiscoveryConfig,
    GCPRemoteService,
)
from cloud_engineer_mcp.discovery import (
    aws_knowledge_account,
    expand_backends,
    github_remote_account_if_configured,
    microsoft_learn_account,
)


class TestHeaderResolution:
    def test_env_var_interpolation(self, monkeypatch) -> None:
        from cloud_engineer_mcp.backends.process import _resolve_headers

        monkeypatch.setenv("MY_TOKEN", "secret-abc")
        resolved = _resolve_headers({"Authorization": "Bearer ${MY_TOKEN}"})
        assert resolved == {"Authorization": "Bearer secret-abc"}

    def test_unset_env_var_kept_as_placeholder(self, monkeypatch) -> None:
        from cloud_engineer_mcp.backends.process import _resolve_headers

        monkeypatch.delenv("ABSENT_VAR", raising=False)
        resolved = _resolve_headers({"X": "Bearer ${ABSENT_VAR}"})
        assert resolved == {"X": "Bearer ${ABSENT_VAR}"}

    def test_no_placeholder_unchanged(self) -> None:
        from cloud_engineer_mcp.backends.process import _resolve_headers

        assert _resolve_headers({"X": "plain"}) == {"X": "plain"}


class TestGithubRemote:
    def test_account_when_token_set(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        acct = github_remote_account_if_configured(
            "https://api.githubcopilot.com/mcp", "GITHUB_TOKEN"
        )
        assert acct is not None
        assert acct.provider == "github_remote"
        assert acct.env_vars["_REMOTE_URL"] == "https://api.githubcopilot.com/mcp"
        assert acct.env_vars["_REMOTE_HEADER_Authorization"] == "Bearer ghp_xxx"

    def test_account_none_without_token(self, monkeypatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        acct = github_remote_account_if_configured(
            "https://api.githubcopilot.com/mcp", "GITHUB_TOKEN"
        )
        assert acct is None


class TestAnonymousRemotes:
    def test_microsoft_learn_account(self) -> None:
        acct = microsoft_learn_account("https://learn.microsoft.com/api/mcp")
        assert acct.provider == "microsoft_learn"
        assert "_REMOTE_HEADER_Authorization" not in acct.env_vars

    def test_aws_knowledge_account(self) -> None:
        acct = aws_knowledge_account("https://knowledge-mcp.global.api.aws")
        assert acct.provider == "aws_knowledge"


class TestExpandBackendsRemote:
    def test_github_remote_expands_to_http_backend(self) -> None:
        from cloud_engineer_mcp.discovery import DiscoveredAccount

        accounts = [
            DiscoveredAccount(
                provider="github_remote",
                profile_id="default",
                display_name="GitHub (remote)",
                env_vars={
                    "_REMOTE_URL": "https://api.githubcopilot.com/mcp",
                    "_REMOTE_HEADER_Authorization": "Bearer ghp_xxx",
                },
                credentials_valid=True,
            )
        ]
        backends = expand_backends(accounts, DiscoveryConfig(), {})
        assert len(backends) == 1
        bid, cfg = next(iter(backends.items()))
        assert bid.startswith("gh_remote_")
        assert cfg.transport == "http"
        assert cfg.url == "https://api.githubcopilot.com/mcp"
        assert cfg.headers == {"Authorization": "Bearer ghp_xxx"}
        assert cfg.command == ""  # no subprocess

    def test_microsoft_learn_expands(self) -> None:
        from cloud_engineer_mcp.discovery import DiscoveredAccount

        accounts = [
            DiscoveredAccount(
                provider="microsoft_learn",
                profile_id="default",
                display_name="Microsoft Learn",
                env_vars={"_REMOTE_URL": "https://learn.microsoft.com/api/mcp"},
                credentials_valid=True,
            )
        ]
        backends = expand_backends(accounts, DiscoveryConfig(), {})
        bid, cfg = next(iter(backends.items()))
        assert bid.startswith("mslearn_")
        assert cfg.transport == "http"
        assert cfg.headers == {}


@pytest.mark.asyncio
class TestGCPRemoteToken:
    async def test_env_token_preferred(self, monkeypatch) -> None:
        from cloud_engineer_mcp.discovery import _fetch_gcp_token

        monkeypatch.setenv("MY_GCP_TOKEN", "env-token")
        token = await _fetch_gcp_token("MY_GCP_TOKEN", ["never", "runs"])
        assert token == "env-token"

    async def test_command_used_when_no_env(self) -> None:
        from cloud_engineer_mcp.discovery import _fetch_gcp_token

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"cmd-token\n", b"")
        mock_proc.returncode = 0
        with (
            patch(
                "cloud_engineer_mcp.discovery.shutil.which",
                return_value="/usr/bin/gcloud",
            ),
            patch(
                "cloud_engineer_mcp.discovery.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            token = await _fetch_gcp_token(None, ["gcloud", "auth", "print-access-token"])
        assert token == "cmd-token"

    async def test_command_failure_returns_none(self) -> None:
        from cloud_engineer_mcp.discovery import _fetch_gcp_token

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"ERROR")
        mock_proc.returncode = 1
        with (
            patch(
                "cloud_engineer_mcp.discovery.shutil.which",
                return_value="/usr/bin/gcloud",
            ),
            patch(
                "cloud_engineer_mcp.discovery.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            token = await _fetch_gcp_token(None, ["gcloud", "auth"])
        assert token is None


@pytest.mark.asyncio
class TestGCPRemoteAccounts:
    async def test_one_account_per_service(self, monkeypatch) -> None:
        from cloud_engineer_mcp.discovery import gcp_remote_accounts

        monkeypatch.setenv("MY_GCP_TOKEN", "token-1")
        services = [
            GCPRemoteService(name="bigquery", url="https://bigquery.googleapis.com/mcp"),
            GCPRemoteService(name="gke", url="https://container.googleapis.com/mcp"),
        ]
        accounts = await gcp_remote_accounts(services, "MY_GCP_TOKEN", [])
        assert len(accounts) == 2
        for a in accounts:
            assert a.provider == "gcp_remote"
            assert a.env_vars["_REMOTE_HEADER_Authorization"] == "Bearer token-1"

    async def test_no_services_no_accounts(self, monkeypatch) -> None:
        from cloud_engineer_mcp.discovery import gcp_remote_accounts

        monkeypatch.setenv("X", "tok")
        assert await gcp_remote_accounts([], "X", []) == []

    async def test_token_command_path_uses_refresh_flow(self, monkeypatch) -> None:
        """When token_env is unset and token_command is set, the resulting
        account carries _REMOTE_AUTH_COMMAND so expand_backends installs the
        rotating CommandTokenAuth flow instead of stamping a static token.
        """
        from cloud_engineer_mcp.discovery import gcp_remote_accounts

        monkeypatch.delenv("UNSET_TOKEN_ENV", raising=False)
        services = [
            GCPRemoteService(name="bq", url="https://bigquery.googleapis.com/mcp"),
        ]
        accounts = await gcp_remote_accounts(
            services,
            token_env=None,
            token_command=["gcloud", "auth", "print-access-token"],
        )
        assert len(accounts) == 1
        # No static header — refresh flow takes over.
        assert "_REMOTE_HEADER_Authorization" not in accounts[0].env_vars
        assert accounts[0].env_vars["_REMOTE_AUTH_COMMAND"] == "gcloud auth print-access-token"


class TestAWSManagedRemote:
    def test_anonymous_account(self) -> None:
        from cloud_engineer_mcp.discovery import aws_managed_account

        acct = aws_managed_account("https://aws-mcp.us-east-1.api.aws/mcp", None)
        assert acct.provider == "aws_managed"
        assert "_REMOTE_HEADER_Authorization" not in acct.env_vars

    def test_with_token(self, monkeypatch) -> None:
        from cloud_engineer_mcp.discovery import aws_managed_account

        monkeypatch.setenv("AWS_MCP_TOKEN", "aws-tok")
        acct = aws_managed_account("https://aws-mcp.us-east-1.api.aws/mcp", "AWS_MCP_TOKEN")
        assert acct.env_vars["_REMOTE_HEADER_Authorization"] == "Bearer aws-tok"


class TestExpandBackendsRefreshFlow:
    def test_gcp_remote_with_auth_command_installs_refresh(self) -> None:
        from cloud_engineer_mcp.discovery import DiscoveredAccount

        accounts = [
            DiscoveredAccount(
                provider="gcp_remote",
                profile_id="bq",
                display_name="GCP managed: bq",
                env_vars={
                    "_REMOTE_URL": "https://bigquery.googleapis.com/mcp",
                    "_REMOTE_AUTH_COMMAND": "gcloud auth print-access-token",
                },
                credentials_valid=True,
            )
        ]
        backends = expand_backends(accounts, DiscoveryConfig(), {})
        bid, cfg = next(iter(backends.items()))
        assert cfg.transport == "http"
        assert cfg.auth_refresh_command == ["gcloud", "auth", "print-access-token"]
        assert cfg.auth_header_name == "Authorization"


class TestManualHttpBackend:
    """Users can declare a remote backend directly under `backends:`."""

    def test_http_backend_passes_through_expand(self) -> None:
        manual = {
            "internal_mcp": BackendConfig(
                display_name="Internal MCP",
                transport="http",
                url="https://mcp.internal.corp/",
                headers={"Authorization": "Bearer ${INTERNAL_MCP_TOKEN}"},
            )
        }
        backends = expand_backends([], DiscoveryConfig(), manual)
        assert backends["internal_mcp"].transport == "http"
        assert backends["internal_mcp"].url == "https://mcp.internal.corp/"
