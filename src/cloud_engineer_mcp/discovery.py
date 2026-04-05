"""Auto-discovery of cloud profiles, subscriptions, and projects with credential validation."""

from __future__ import annotations

import asyncio
import configparser
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from cloud_engineer_mcp.config import BackendConfig
from cloud_engineer_mcp.errors import CredentialError
from cloud_engineer_mcp.observability.logging import get_logger

if TYPE_CHECKING:
    from cloud_engineer_mcp.config import DiscoveryConfig

log = get_logger("discovery")

_SAFE_ID_RE = re.compile(r"[^a-z0-9]")
CLI_TIMEOUT_SECONDS = 10
CREDENTIAL_CHECK_TIMEOUT = 15


@dataclass
class DiscoveredAccount:
    provider: str
    profile_id: str
    display_name: str
    env_vars: dict[str, str] = field(default_factory=dict)
    credentials_valid: bool = True


MAX_BACKEND_ID_LEN = 10


def _safe_id(name: str, max_len: int = MAX_BACKEND_ID_LEN) -> str:
    """Convert a name to a safe backend ID fragment, truncated to max_len."""
    result = _SAFE_ID_RE.sub("_", name.lower()).strip("_")
    if len(result) > max_len:
        result = result[:max_len].rstrip("_")
    return result


# ---------------------------------------------------------------------------
# Credential Validation
# ---------------------------------------------------------------------------

async def _validate_aws_credentials(profile: str) -> bool:
    """Check if AWS credentials are valid by calling sts get-caller-identity."""
    aws_cmd = shutil.which("aws")
    if not aws_cmd:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            aws_cmd, "sts", "get-caller-identity",
            "--profile", profile,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _ = await asyncio.wait_for(
            proc.communicate(), timeout=CREDENTIAL_CHECK_TIMEOUT
        )
        return proc.returncode == 0
    except (TimeoutError, Exception):
        return False


async def _validate_azure_credentials() -> bool:
    """Check if Azure CLI is authenticated by running az account show."""
    az_cmd = shutil.which("az")
    if not az_cmd:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            az_cmd, "account", "show", "--output", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _ = await asyncio.wait_for(
            proc.communicate(), timeout=CREDENTIAL_CHECK_TIMEOUT
        )
        return proc.returncode == 0
    except (TimeoutError, Exception):
        return False


async def _validate_gcp_credentials() -> bool:
    """Check if GCP credentials are valid by printing an access token."""
    gcloud_cmd = shutil.which("gcloud")
    if not gcloud_cmd:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            gcloud_cmd, "auth", "print-access-token",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=CREDENTIAL_CHECK_TIMEOUT
        )
        return proc.returncode == 0 and len(stdout.strip()) > 0
    except (TimeoutError, Exception):
        return False


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------

async def discover_aws_profiles(
    default_region: str = "us-east-1",
    exclude_profiles: list[str] | None = None,
    validate_credentials: bool = True,
) -> list[DiscoveredAccount]:
    """Parse ~/.aws/config to discover all configured AWS profiles.

    If validate_credentials is True, checks each profile with sts get-caller-identity
    and marks accounts with expired/invalid credentials.
    """
    exclude = set(exclude_profiles or [])
    config_path = Path.home() / ".aws" / "config"

    if not config_path.exists():
        log.info("discovery.aws.no_config", path=str(config_path))
        return []

    parser = configparser.ConfigParser()
    parser.read(config_path)

    accounts: list[DiscoveredAccount] = []
    for section in parser.sections():
        if section.startswith("profile "):
            profile_name = section.removeprefix("profile ").strip()
        elif section == "default":
            profile_name = "default"
        else:
            continue

        if profile_name in exclude:
            continue

        props = dict(parser[section])
        region = props.get("region", default_region)
        account_id = props.get("sso_account_id", "")
        display = f"AWS ({profile_name})"
        if account_id:
            display = f"AWS ({profile_name} / {account_id})"

        accounts.append(DiscoveredAccount(
            provider="aws",
            profile_id=profile_name,
            display_name=display,
            env_vars={
                "AWS_PROFILE": profile_name,
                "AWS_REGION": region,
            },
        ))

    if validate_credentials and accounts:
        log.info("discovery.aws.validating_credentials", count=len(accounts))
        tasks = {
            acct.profile_id: asyncio.create_task(_validate_aws_credentials(acct.profile_id))
            for acct in accounts
        }
        for acct in accounts:
            valid = await tasks[acct.profile_id]
            acct.credentials_valid = valid
            if not valid:
                log.warning(
                    "discovery.aws.credentials_invalid",
                    profile=acct.profile_id,
                )

    valid_count = sum(1 for a in accounts if a.credentials_valid)
    log.info("discovery.aws.found", total=len(accounts), valid=valid_count)
    return accounts


# ---------------------------------------------------------------------------
# Azure
# ---------------------------------------------------------------------------

async def discover_azure_subscriptions(
    exclude_subscriptions: list[str] | None = None,
    validate_credentials: bool = True,
) -> list[DiscoveredAccount]:
    """Run `az account list` to discover Azure subscriptions."""
    exclude = set(exclude_subscriptions or [])

    if not shutil.which("az"):
        log.info("discovery.azure.cli_not_found")
        return []

    if validate_credentials:
        creds_ok = await _validate_azure_credentials()
        if not creds_ok:
            log.warning("discovery.azure.credentials_invalid")
            return []

    try:
        proc = await asyncio.create_subprocess_exec(
            "az", "account", "list", "--output", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=CLI_TIMEOUT_SECONDS
        )
    except TimeoutError:
        log.warning("discovery.azure.timeout")
        return []
    except Exception as exc:
        log.warning("discovery.azure.error", error=str(exc))
        return []

    if proc.returncode != 0:
        log.warning("discovery.azure.cli_failed", stderr=stderr.decode()[:200])
        return []

    try:
        subs = json.loads(stdout.decode())
    except json.JSONDecodeError:
        log.warning("discovery.azure.invalid_json")
        return []

    accounts: list[DiscoveredAccount] = []
    for sub in subs:
        if sub.get("state") != "Enabled":
            continue
        name = sub.get("name", "")
        sub_id = sub.get("id", "")
        if name in exclude or sub_id in exclude:
            continue

        accounts.append(DiscoveredAccount(
            provider="azure",
            profile_id=sub_id,
            display_name=f"Azure ({name})",
            env_vars={
                "AZURE_SUBSCRIPTION_ID": sub_id,
                "AZURE_TENANT_ID": sub.get("tenantId", ""),
            },
            credentials_valid=True,
        ))

    log.info("discovery.azure.found", count=len(accounts))
    return accounts


# ---------------------------------------------------------------------------
# GCP
# ---------------------------------------------------------------------------

async def discover_gcp_projects(
    exclude_projects: list[str] | None = None,
    validate_credentials: bool = True,
) -> list[DiscoveredAccount]:
    """Run `gcloud projects list` to discover GCP projects."""
    exclude = set(exclude_projects or [])

    if not shutil.which("gcloud"):
        log.info("discovery.gcp.cli_not_found")
        return []

    if validate_credentials:
        creds_ok = await _validate_gcp_credentials()
        if not creds_ok:
            log.warning("discovery.gcp.credentials_invalid")
            return []

    try:
        proc = await asyncio.create_subprocess_exec(
            "gcloud", "projects", "list", "--format=json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=CLI_TIMEOUT_SECONDS
        )
    except TimeoutError:
        log.warning("discovery.gcp.timeout")
        return []
    except Exception as exc:
        log.warning("discovery.gcp.error", error=str(exc))
        return []

    if proc.returncode != 0:
        log.warning("discovery.gcp.cli_failed", stderr=stderr.decode()[:200])
        return []

    try:
        projects = json.loads(stdout.decode())
    except json.JSONDecodeError:
        log.warning("discovery.gcp.invalid_json")
        return []

    accounts: list[DiscoveredAccount] = []
    for proj in projects:
        lifecycle = proj.get("lifecycleState", "")
        if lifecycle != "ACTIVE":
            continue
        project_id = proj.get("projectId", "")
        name = proj.get("name", project_id)
        if project_id in exclude or name in exclude:
            continue

        accounts.append(DiscoveredAccount(
            provider="gcp",
            profile_id=project_id,
            display_name=f"GCP ({name})",
            env_vars={
                "GCLOUD_PROJECT": project_id,
            },
            credentials_valid=True,
        ))

    log.info("discovery.gcp.found", count=len(accounts))
    return accounts


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def discover_all(
    config: DiscoveryConfig,
    require_credentials: bool = True,
) -> list[DiscoveredAccount]:
    """Run all enabled discovery providers and return combined results.

    Args:
        config: Discovery configuration.
        require_credentials: If True, raises CredentialError when no valid
            credentials are found across all enabled providers.
    """
    if not config.enabled:
        return []

    all_accounts: list[DiscoveredAccount] = []
    enabled_providers: list[str] = []
    failed_providers: list[str] = []

    if config.aws.enabled:
        enabled_providers.append("aws")
        aws = await discover_aws_profiles(
            default_region=config.aws.default_region,
            exclude_profiles=config.aws.exclude_profiles,
            validate_credentials=require_credentials,
        )
        valid_aws = [a for a in aws if a.credentials_valid]
        invalid_aws = [a for a in aws if not a.credentials_valid]
        if invalid_aws:
            profiles = ", ".join(a.profile_id for a in invalid_aws)
            log.warning(
                "discovery.aws.skipping_invalid",
                count=len(invalid_aws),
                profiles=profiles,
            )
        if not valid_aws and aws:
            failed_providers.append("aws")
        all_accounts.extend(valid_aws)

    tasks: list[tuple[str, asyncio.Task]] = []
    if config.azure.enabled:
        enabled_providers.append("azure")
        tasks.append(("azure", asyncio.create_task(
            discover_azure_subscriptions(
                config.azure.exclude_subscriptions,
                validate_credentials=require_credentials,
            )
        )))
    if config.gcp.enabled:
        enabled_providers.append("gcp")
        tasks.append(("gcp", asyncio.create_task(
            discover_gcp_projects(
                config.gcp.exclude_projects,
                validate_credentials=require_credentials,
            )
        )))

    for provider, task in tasks:
        try:
            results = await task
            if not results and require_credentials:
                failed_providers.append(provider)
            all_accounts.extend(results)
        except Exception as exc:
            log.warning(f"discovery.{provider}.unexpected_error", error=str(exc))
            failed_providers.append(provider)

    log.info(
        "discovery.complete",
        total=len(all_accounts),
        enabled_providers=enabled_providers,
        failed_providers=failed_providers,
    )

    if require_credentials and not all_accounts and enabled_providers:
        hints = []
        if "aws" in failed_providers:
            hints.append("AWS: run 'aws sso login' to refresh SSO tokens")
        if "azure" in failed_providers:
            hints.append("Azure: run 'az login' to authenticate")
        if "gcp" in failed_providers:
            hints.append("GCP: run 'gcloud auth login' to authenticate")
        raise CredentialError(
            providers=failed_providers or enabled_providers,
            details=". ".join(hints) if hints else "Check your cloud CLI configurations",
        )

    return all_accounts


def _resolve_aws_command(mcp_server: str) -> tuple[str, list[str]]:
    """Resolve AWS backend command: use pre-installed binary if available, else uvx."""
    from cloud_engineer_mcp.installer import get_installed_binary

    binary = get_installed_binary(mcp_server)
    if binary:
        return binary, []
    return "uvx", [mcp_server]


def _resolve_npm_command(
    package: str, extra_args: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Resolve npm-based backend: use pre-installed binary if available, else npx."""
    from cloud_engineer_mcp.installer import get_installed_binary

    pkg_name = package.replace("npx -y ", "").replace(" server start", "").strip()
    binary = get_installed_binary(pkg_name)
    if binary:
        return binary, list(extra_args or [])
    return "npx", ["-y", pkg_name, *(extra_args or [])]


def expand_backends(
    discovered: list[DiscoveredAccount],
    config: DiscoveryConfig,
    manual_backends: dict[str, BackendConfig],
) -> dict[str, BackendConfig]:
    """Convert discovered accounts into BackendConfig entries and merge with manual backends.

    Only accounts with valid credentials are expanded.
    Uses pre-installed binaries when available for faster startup.
    Manual backends take precedence on name collision.
    """
    backends: dict[str, BackendConfig] = {}
    gcp_accounts: list[DiscoveredAccount] = []

    for account in discovered:
        if not account.credentials_valid:
            continue

        if account.provider == "aws":
            aws_cfg = config.aws
            backend_id = f"aws_{_safe_id(account.profile_id)}"
            command, args = _resolve_aws_command(aws_cfg.mcp_server)
            env = {
                **account.env_vars,
                "FASTMCP_LOG_LEVEL": "WARNING",
            }
            backends[backend_id] = BackendConfig(
                display_name=account.display_name,
                command=command,
                args=args,
                env=env,
                enabled=True,
                restart_on_failure=True,
                max_restarts=3,
                startup_timeout_seconds=aws_cfg.startup_timeout_seconds,
                health_check_interval_seconds=60,
            )

        elif account.provider == "azure":
            az_cfg = config.azure
            backend_id = f"az_{_safe_id(account.profile_id, max_len=15)}"
            mcp_parts = az_cfg.mcp_command.split()
            pkg = " ".join(p for p in mcp_parts if p.startswith("@"))
            extra = [p for p in mcp_parts if not p.startswith(("npx", "-y", "@"))]
            command, args = _resolve_npm_command(pkg, extra)
            backends[backend_id] = BackendConfig(
                display_name=account.display_name,
                command=command,
                args=args,
                env=account.env_vars,
                enabled=True,
                restart_on_failure=True,
                max_restarts=3,
                startup_timeout_seconds=az_cfg.startup_timeout_seconds,
                health_check_interval_seconds=60,
            )

        elif account.provider == "gcp":
            gcp_accounts.append(account)

    if gcp_accounts:
        gcp_cfg = config.gcp
        command, args = _resolve_npm_command(gcp_cfg.mcp_server)
        first = gcp_accounts[0]
        backends["gcp"] = BackendConfig(
            display_name=f"GCP ({len(gcp_accounts)} projects)",
            command=command,
            args=args,
            env=first.env_vars,
            enabled=True,
            restart_on_failure=True,
            max_restarts=3,
            startup_timeout_seconds=gcp_cfg.startup_timeout_seconds,
            health_check_interval_seconds=60,
        )

    for bid, cfg in manual_backends.items():
        backends[bid] = cfg

    return backends
