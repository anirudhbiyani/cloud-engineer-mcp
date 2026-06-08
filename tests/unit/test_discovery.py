"""Tests for the auto-discovery module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cloud_engineer_mcp.config import (
    AWSDiscoveryConfig,
    AzureDiscoveryConfig,
    BackendConfig,
    DiscoveryConfig,
    GCPDiscoveryConfig,
)
from cloud_engineer_mcp.discovery import (
    DiscoveredAccount,
    _safe_id,
    discover_all,
    discover_aws_profiles,
    discover_azure_subscriptions,
    discover_gcp_projects,
    expand_backends,
)
from cloud_engineer_mcp.errors import CredentialError


class TestSafeId:
    def test_simple_name(self) -> None:
        assert _safe_id("security") == "security"

    def test_spaces_and_special_chars(self) -> None:
        assert _safe_id("Azure subscription 1") == "azure_subs"

    def test_dashes(self) -> None:
        assert _safe_id("my-project-id") == "my_project"

    def test_mixed(self) -> None:
        assert _safe_id("Prof-Prod (123)") == "prof_prod"

    def test_custom_max_len(self) -> None:
        assert _safe_id("my-long-project-name", max_len=20) == "my_long_project_name"


AWS_CONFIG_CONTENT = """\
[profile dns]
sso_session = my-sso
sso_account_id = 111111111111
sso_role_name = AdministratorAccess
region = us-west-2

[profile management]
sso_session = my-sso
sso_account_id = 222222222222
sso_role_name = AdministratorAccess

[profile security]
sso_session = my-sso
sso_account_id = 333333333333
sso_role_name = AdministratorAccess

[sso-session my-sso]
sso_start_url = https://example.awsapps.com/start/
sso_region = us-west-2
"""


class TestDiscoverAWSProfiles:
    @pytest.mark.asyncio
    async def test_parses_profiles_no_validation(self, tmp_path: Path) -> None:
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        (aws_dir / "config").write_text(AWS_CONFIG_CONTENT)

        with patch("cloud_engineer_mcp.discovery.Path.home", return_value=tmp_path):
            profiles = await discover_aws_profiles(
                default_region="us-east-1", validate_credentials=False
            )

        assert len(profiles) == 3
        names = [p.profile_id for p in profiles]
        assert "dns" in names
        assert "management" in names
        assert "security" in names

        dns = next(p for p in profiles if p.profile_id == "dns")
        assert dns.env_vars["AWS_PROFILE"] == "dns"
        assert dns.env_vars["AWS_REGION"] == "us-west-2"
        assert "111111111111" in dns.display_name
        assert dns.credentials_valid is True

    @pytest.mark.asyncio
    async def test_default_region_applied(self, tmp_path: Path) -> None:
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        (aws_dir / "config").write_text(AWS_CONFIG_CONTENT)

        with patch("cloud_engineer_mcp.discovery.Path.home", return_value=tmp_path):
            profiles = await discover_aws_profiles(
                default_region="us-east-1", validate_credentials=False
            )

        mgmt = next(p for p in profiles if p.profile_id == "management")
        assert mgmt.env_vars["AWS_REGION"] == "us-east-1"

    @pytest.mark.asyncio
    async def test_exclude_profiles(self, tmp_path: Path) -> None:
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        (aws_dir / "config").write_text(AWS_CONFIG_CONTENT)

        with patch("cloud_engineer_mcp.discovery.Path.home", return_value=tmp_path):
            profiles = await discover_aws_profiles(
                exclude_profiles=["dns", "security"], validate_credentials=False
            )

        assert len(profiles) == 1
        assert profiles[0].profile_id == "management"

    @pytest.mark.asyncio
    async def test_no_config_file(self, tmp_path: Path) -> None:
        with patch("cloud_engineer_mcp.discovery.Path.home", return_value=tmp_path):
            profiles = await discover_aws_profiles(validate_credentials=False)

        assert profiles == []

    @pytest.mark.asyncio
    async def test_sso_session_section_ignored(self, tmp_path: Path) -> None:
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        (aws_dir / "config").write_text(AWS_CONFIG_CONTENT)

        with patch("cloud_engineer_mcp.discovery.Path.home", return_value=tmp_path):
            profiles = await discover_aws_profiles(validate_credentials=False)

        profile_ids = [p.profile_id for p in profiles]
        assert "my-sso" not in profile_ids

    @pytest.mark.asyncio
    async def test_validates_credentials(self, tmp_path: Path) -> None:
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        (aws_dir / "config").write_text(AWS_CONFIG_CONTENT)

        async def fake_validate(profile: str) -> bool:
            return profile == "security"

        with (
            patch("cloud_engineer_mcp.discovery.Path.home", return_value=tmp_path),
            patch(
                "cloud_engineer_mcp.discovery._validate_aws_credentials",
                side_effect=fake_validate,
            ),
        ):
            profiles = await discover_aws_profiles(validate_credentials=True)

        valid = [p for p in profiles if p.credentials_valid]
        invalid = [p for p in profiles if not p.credentials_valid]
        assert len(valid) == 1
        assert valid[0].profile_id == "security"
        assert len(invalid) == 2


AZURE_SUBS_JSON = json.dumps(
    [
        {
            "name": "standard",
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "tenantId": "11111111-2222-3333-4444-555555555555",
            "state": "Enabled",
            "isDefault": False,
        },
        {
            "name": "Azure subscription 1",
            "id": "ffffffff-aaaa-bbbb-cccc-dddddddddddd",
            "tenantId": "11111111-2222-3333-4444-555555555555",
            "state": "Enabled",
            "isDefault": True,
        },
        {
            "name": "disabled-sub",
            "id": "00000000-0000-0000-0000-000000000000",
            "tenantId": "11111111-2222-3333-4444-555555555555",
            "state": "Disabled",
            "isDefault": False,
        },
    ]
)


class TestDiscoverAzureSubscriptions:
    @pytest.mark.asyncio
    async def test_parses_subscriptions(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (AZURE_SUBS_JSON.encode(), b"")
        mock_proc.returncode = 0

        with (
            patch("cloud_engineer_mcp.discovery.shutil.which", return_value="/usr/bin/az"),
            patch(
                "cloud_engineer_mcp.discovery.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            subs = await discover_azure_subscriptions(validate_credentials=False)

        assert len(subs) == 2
        names = [s.display_name for s in subs]
        assert "Azure (standard)" in names
        assert "Azure (Azure subscription 1)" in names

    @pytest.mark.asyncio
    async def test_disabled_sub_excluded(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (AZURE_SUBS_JSON.encode(), b"")
        mock_proc.returncode = 0

        with (
            patch("cloud_engineer_mcp.discovery.shutil.which", return_value="/usr/bin/az"),
            patch(
                "cloud_engineer_mcp.discovery.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            subs = await discover_azure_subscriptions(validate_credentials=False)

        sub_ids = [s.profile_id for s in subs]
        assert "00000000-0000-0000-0000-000000000000" not in sub_ids

    @pytest.mark.asyncio
    async def test_exclude_subscriptions(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (AZURE_SUBS_JSON.encode(), b"")
        mock_proc.returncode = 0

        with (
            patch("cloud_engineer_mcp.discovery.shutil.which", return_value="/usr/bin/az"),
            patch(
                "cloud_engineer_mcp.discovery.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            subs = await discover_azure_subscriptions(
                exclude_subscriptions=["standard"], validate_credentials=False
            )

        assert len(subs) == 1

    @pytest.mark.asyncio
    async def test_az_not_installed(self) -> None:
        with patch("cloud_engineer_mcp.discovery.shutil.which", return_value=None):
            subs = await discover_azure_subscriptions()
        assert subs == []

    @pytest.mark.asyncio
    async def test_az_failure(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error")
        mock_proc.returncode = 1

        with (
            patch("cloud_engineer_mcp.discovery.shutil.which", return_value="/usr/bin/az"),
            patch(
                "cloud_engineer_mcp.discovery._validate_azure_credentials",
                return_value=True,
            ),
            patch(
                "cloud_engineer_mcp.discovery.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            subs = await discover_azure_subscriptions(validate_credentials=True)

        assert subs == []

    @pytest.mark.asyncio
    async def test_invalid_credentials_returns_empty(self) -> None:
        with (
            patch("cloud_engineer_mcp.discovery.shutil.which", return_value="/usr/bin/az"),
            patch(
                "cloud_engineer_mcp.discovery._validate_azure_credentials",
                return_value=False,
            ),
        ):
            subs = await discover_azure_subscriptions(validate_credentials=True)
        assert subs == []


GCP_PROJECTS_JSON = json.dumps(
    [
        {
            "projectId": "my-project-1",
            "name": "My Project 1",
            "lifecycleState": "ACTIVE",
        },
        {
            "projectId": "my-project-2",
            "name": "My Project 2",
            "lifecycleState": "ACTIVE",
        },
        {
            "projectId": "deleted-proj",
            "name": "Deleted",
            "lifecycleState": "DELETE_REQUESTED",
        },
    ]
)


class TestDiscoverGCPProjects:
    @pytest.mark.asyncio
    async def test_parses_projects(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (GCP_PROJECTS_JSON.encode(), b"")
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
            projects = await discover_gcp_projects(validate_credentials=False)

        assert len(projects) == 2
        ids = [p.profile_id for p in projects]
        assert "my-project-1" in ids
        assert "deleted-proj" not in ids

    @pytest.mark.asyncio
    async def test_exclude_projects(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (GCP_PROJECTS_JSON.encode(), b"")
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
            projects = await discover_gcp_projects(
                exclude_projects=["my-project-1"], validate_credentials=False
            )

        assert len(projects) == 1
        assert projects[0].profile_id == "my-project-2"

    @pytest.mark.asyncio
    async def test_gcloud_not_installed(self) -> None:
        with patch("cloud_engineer_mcp.discovery.shutil.which", return_value=None):
            projects = await discover_gcp_projects()
        assert projects == []

    @pytest.mark.asyncio
    async def test_invalid_credentials_returns_empty(self) -> None:
        with (
            patch(
                "cloud_engineer_mcp.discovery.shutil.which",
                return_value="/usr/bin/gcloud",
            ),
            patch(
                "cloud_engineer_mcp.discovery._validate_gcp_credentials",
                return_value=False,
            ),
        ):
            projects = await discover_gcp_projects(validate_credentials=True)
        assert projects == []


class TestDiscoverAllCredentialError:
    @pytest.mark.asyncio
    async def test_raises_when_no_valid_credentials(self, tmp_path: Path) -> None:
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        (aws_dir / "config").write_text(AWS_CONFIG_CONTENT)

        config = DiscoveryConfig(
            aws=AWSDiscoveryConfig(enabled=True),
            azure=AzureDiscoveryConfig(enabled=False),
            gcp=GCPDiscoveryConfig(enabled=False),
        )

        async def always_invalid(profile: str) -> bool:
            return False

        with (
            patch("cloud_engineer_mcp.discovery.Path.home", return_value=tmp_path),
            patch(
                "cloud_engineer_mcp.discovery._validate_aws_credentials",
                side_effect=always_invalid,
            ),
            pytest.raises(CredentialError) as exc_info,
        ):
            await discover_all(config, require_credentials=True)

        assert "aws" in exc_info.value.providers
        assert "aws sso login" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_no_error_when_some_valid(self, tmp_path: Path) -> None:
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        (aws_dir / "config").write_text(AWS_CONFIG_CONTENT)

        config = DiscoveryConfig(
            aws=AWSDiscoveryConfig(enabled=True),
            azure=AzureDiscoveryConfig(enabled=False),
            gcp=GCPDiscoveryConfig(enabled=False),
        )

        async def one_valid(profile: str) -> bool:
            return profile == "security"

        with (
            patch("cloud_engineer_mcp.discovery.Path.home", return_value=tmp_path),
            patch(
                "cloud_engineer_mcp.discovery._validate_aws_credentials",
                side_effect=one_valid,
            ),
        ):
            results = await discover_all(config, require_credentials=True)

        assert len(results) == 1
        assert results[0].profile_id == "security"

    @pytest.mark.asyncio
    async def test_no_error_when_require_false(self, tmp_path: Path) -> None:
        config = DiscoveryConfig(
            aws=AWSDiscoveryConfig(enabled=True),
            azure=AzureDiscoveryConfig(enabled=False),
            gcp=GCPDiscoveryConfig(enabled=False),
        )

        with patch("cloud_engineer_mcp.discovery.Path.home", return_value=tmp_path):
            results = await discover_all(config, require_credentials=False)

        assert results == []


KUBECTL_CONTEXTS = "prod-cluster\nstaging-cluster\ndev-laptop\n"


class TestDiscoverKubernetes:
    @pytest.mark.asyncio
    async def test_parses_contexts(self) -> None:
        from cloud_engineer_mcp.discovery import discover_kubernetes_contexts

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (KUBECTL_CONTEXTS.encode(), b"")
        mock_proc.returncode = 0

        with (
            patch(
                "cloud_engineer_mcp.discovery.shutil.which",
                return_value="/usr/local/bin/kubectl",
            ),
            patch(
                "cloud_engineer_mcp.discovery.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            ctxs = await discover_kubernetes_contexts()

        assert len(ctxs) == 3
        names = [c.profile_id for c in ctxs]
        assert "prod-cluster" in names
        assert "dev-laptop" in names
        assert all(c.provider == "kubernetes" for c in ctxs)

    @pytest.mark.asyncio
    async def test_exclude_contexts(self) -> None:
        from cloud_engineer_mcp.discovery import discover_kubernetes_contexts

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (KUBECTL_CONTEXTS.encode(), b"")
        mock_proc.returncode = 0

        with (
            patch(
                "cloud_engineer_mcp.discovery.shutil.which",
                return_value="/usr/local/bin/kubectl",
            ),
            patch(
                "cloud_engineer_mcp.discovery.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            ctxs = await discover_kubernetes_contexts(exclude_contexts=["dev-laptop"])

        assert {c.profile_id for c in ctxs} == {"prod-cluster", "staging-cluster"}

    @pytest.mark.asyncio
    async def test_kubectl_not_installed(self) -> None:
        from cloud_engineer_mcp.discovery import discover_kubernetes_contexts

        with patch("cloud_engineer_mcp.discovery.shutil.which", return_value=None):
            ctxs = await discover_kubernetes_contexts()

        assert ctxs == []


class TestTokenGatedProviders:
    def test_cloudflare_account_when_set(self) -> None:
        from cloud_engineer_mcp.discovery import cloudflare_account_if_configured

        with patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "tok"}, clear=False):
            acct = cloudflare_account_if_configured("CLOUDFLARE_API_TOKEN")

        assert acct is not None
        assert acct.provider == "cloudflare"
        assert acct.env_vars["CLOUDFLARE_API_TOKEN"] == "tok"

    def test_cloudflare_account_when_unset(self) -> None:
        from cloud_engineer_mcp.discovery import cloudflare_account_if_configured

        with patch.dict("os.environ", {}, clear=True):
            acct = cloudflare_account_if_configured("CLOUDFLARE_API_TOKEN")

        assert acct is None

    def test_digitalocean_account_when_set(self) -> None:
        from cloud_engineer_mcp.discovery import digitalocean_account_if_configured

        with patch.dict("os.environ", {"DIGITALOCEAN_TOKEN": "do-tok"}, clear=False):
            acct = digitalocean_account_if_configured("DIGITALOCEAN_TOKEN")

        assert acct is not None
        assert acct.provider == "digitalocean"


class TestExpandBackends:
    def test_expands_aws(self) -> None:
        discovered = [
            DiscoveredAccount(
                "aws",
                "security",
                "AWS (security)",
                {"AWS_PROFILE": "security", "AWS_REGION": "us-east-1"},
            ),
            DiscoveredAccount(
                "aws",
                "staging",
                "AWS (staging)",
                {"AWS_PROFILE": "staging", "AWS_REGION": "us-east-1"},
            ),
        ]
        config = DiscoveryConfig()
        backends = expand_backends(discovered, config, {})

        assert "aws_security" in backends
        assert "aws_staging" in backends
        assert backends["aws_security"].env["AWS_PROFILE"] == "security"
        assert (
            "ccapi-mcp-server" in backends["aws_security"].command
            or backends["aws_security"].command == "uvx"
        )

    def test_skips_invalid_credentials(self) -> None:
        discovered = [
            DiscoveredAccount(
                "aws",
                "valid",
                "AWS (valid)",
                {"AWS_PROFILE": "valid"},
                credentials_valid=True,
            ),
            DiscoveredAccount(
                "aws",
                "invalid",
                "AWS (invalid)",
                {"AWS_PROFILE": "invalid"},
                credentials_valid=False,
            ),
        ]
        config = DiscoveryConfig()
        backends = expand_backends(discovered, config, {})

        assert "aws_valid" in backends
        assert "aws_invalid" not in backends

    def test_expands_azure(self) -> None:
        discovered = [
            DiscoveredAccount(
                "azure",
                "sub-id-123",
                "Azure (standard)",
                {"AZURE_SUBSCRIPTION_ID": "sub-id-123"},
            ),
        ]
        config = DiscoveryConfig()
        backends = expand_backends(discovered, config, {})

        assert len(backends) == 1
        key = list(backends.keys())[0]
        assert key.startswith("az_")
        assert backends[key].command == "npx"

    def test_expands_gcp_single_backend(self) -> None:
        discovered = [
            DiscoveredAccount(
                "gcp", "my-project-1", "GCP (proj1)", {"GCLOUD_PROJECT": "my-project-1"}
            ),
            DiscoveredAccount(
                "gcp", "my-project-2", "GCP (proj2)", {"GCLOUD_PROJECT": "my-project-2"}
            ),
        ]
        config = DiscoveryConfig()
        backends = expand_backends(discovered, config, {})

        assert "gcp" in backends
        assert len([k for k in backends if k.startswith("gcp")]) == 1
        assert "2 projects" in backends["gcp"].display_name

    def test_manual_overrides_discovered(self) -> None:
        discovered = [
            DiscoveredAccount(
                "aws",
                "security",
                "AWS (security)",
                {"AWS_PROFILE": "security", "AWS_REGION": "us-east-1"},
            ),
        ]
        manual = {
            "aws_security": BackendConfig(
                display_name="Custom Override",
                command="custom-cmd",
                args=["--custom"],
            ),
        }
        config = DiscoveryConfig()
        backends = expand_backends(discovered, config, manual)

        assert backends["aws_security"].display_name == "Custom Override"
        assert backends["aws_security"].command == "custom-cmd"

    def test_empty_discovered(self) -> None:
        manual = {
            "my_backend": BackendConfig(display_name="Manual", command="echo"),
        }
        config = DiscoveryConfig()
        backends = expand_backends([], config, manual)

        assert len(backends) == 1
        assert "my_backend" in backends

    def test_aws_extra_servers_spawn_per_profile(self) -> None:
        discovered = [
            DiscoveredAccount(
                "aws",
                "prod",
                "AWS (prod)",
                {"AWS_PROFILE": "prod", "AWS_REGION": "us-east-1"},
            ),
            DiscoveredAccount(
                "aws",
                "staging",
                "AWS (staging)",
                {"AWS_PROFILE": "staging", "AWS_REGION": "us-east-1"},
            ),
        ]
        from cloud_engineer_mcp.config import AWSDiscoveryConfig

        config = DiscoveryConfig(
            aws=AWSDiscoveryConfig(
                extra_servers=[
                    "awslabs.lambda-tool-mcp-server@latest",
                    "awslabs.dynamodb-mcp-server@latest",
                ],
            ),
        )
        backends = expand_backends(discovered, config, {})

        # primary + 2 extras × 2 profiles = 6 backends
        aws_backends = [bid for bid in backends if bid.startswith("aws_")]
        assert len(aws_backends) == 6
        # Spot-check the tags
        assert any("lambda" in bid for bid in aws_backends)
        assert any("dynamodb" in bid for bid in aws_backends)
        # Display names should attribute each extra
        lambda_backend = next((b for bid, b in backends.items() if "lambda" in bid), None)
        assert lambda_backend is not None
        assert "lambda" in lambda_backend.display_name.lower()

    def test_aws_extra_servers_empty_keeps_one_per_profile(self) -> None:
        discovered = [
            DiscoveredAccount(
                "aws",
                "prod",
                "AWS (prod)",
                {"AWS_PROFILE": "prod"},
            ),
        ]
        config = DiscoveryConfig()  # extra_servers defaults to []
        backends = expand_backends(discovered, config, {})
        aws_backends = [bid for bid in backends if bid.startswith("aws_")]
        assert len(aws_backends) == 1

    def test_aws_server_tag(self) -> None:
        from cloud_engineer_mcp.discovery import _aws_server_tag

        assert _aws_server_tag("awslabs.lambda-tool-mcp-server@latest") == "lambda"
        assert _aws_server_tag("awslabs.dynamodb-mcp-server") == "dynamodb"
        assert _aws_server_tag("awslabs.bedrock-kb-retrieval-mcp-server@latest") == "bedrock_kb"

    def test_expands_kubernetes(self) -> None:
        discovered = [
            DiscoveredAccount(
                "kubernetes",
                "prod-cluster",
                "Kubernetes (prod-cluster)",
                {"KUBE_CONTEXT": "prod-cluster"},
            ),
            DiscoveredAccount(
                "kubernetes",
                "staging-cluster",
                "Kubernetes (staging-cluster)",
                {"KUBE_CONTEXT": "staging-cluster"},
            ),
        ]
        config = DiscoveryConfig()
        backends = expand_backends(discovered, config, {})
        ids = list(backends.keys())
        assert any(i.startswith("k8s_") for i in ids)
        assert len([i for i in ids if i.startswith("k8s_")]) == 2

    def test_expands_cloudflare_single(self) -> None:
        discovered = [
            DiscoveredAccount(
                "cloudflare",
                "default",
                "Cloudflare",
                {"CLOUDFLARE_API_TOKEN": "x"},
            ),
        ]
        backends = expand_backends(discovered, DiscoveryConfig(), {})
        assert "cloudflare" in backends
        assert backends["cloudflare"].env["CLOUDFLARE_API_TOKEN"] == "x"

    def test_expands_digitalocean_single(self) -> None:
        discovered = [
            DiscoveredAccount(
                "digitalocean",
                "default",
                "DigitalOcean",
                {"DIGITALOCEAN_TOKEN": "x"},
            ),
        ]
        backends = expand_backends(discovered, DiscoveryConfig(), {})
        assert "digitalocean" in backends

    def test_mixed_providers(self) -> None:
        discovered = [
            DiscoveredAccount("aws", "prod", "AWS (prod)", {"AWS_PROFILE": "prod"}),
            DiscoveredAccount("azure", "sub1", "Azure (sub1)", {"AZURE_SUBSCRIPTION_ID": "sub1"}),
            DiscoveredAccount("gcp", "proj1", "GCP (proj1)", {"GCLOUD_PROJECT": "proj1"}),
        ]
        config = DiscoveryConfig()
        backends = expand_backends(discovered, config, {})

        assert len(backends) == 3
        commands = {b.command for b in backends.values()}
        assert any("ccapi" in c or c == "uvx" for c in commands)
        assert any(c == "npx" or "gcloud" in c for c in commands)
