"""Auto-discovery of cloud profiles, subscriptions, and projects with credential validation."""

from __future__ import annotations

import asyncio
import configparser
import json
import os
import re
import shutil
from collections.abc import Sequence
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
            aws_cmd,
            "sts",
            "get-caller-identity",
            "--profile",
            profile,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _ = await asyncio.wait_for(proc.communicate(), timeout=CREDENTIAL_CHECK_TIMEOUT)
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
            az_cmd,
            "account",
            "show",
            "--output",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _ = await asyncio.wait_for(proc.communicate(), timeout=CREDENTIAL_CHECK_TIMEOUT)
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
            gcloud_cmd,
            "auth",
            "print-access-token",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=CREDENTIAL_CHECK_TIMEOUT)
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

        accounts.append(
            DiscoveredAccount(
                provider="aws",
                profile_id=profile_name,
                display_name=display,
                env_vars={
                    "AWS_PROFILE": profile_name,
                    "AWS_REGION": region,
                },
            )
        )

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
            "az",
            "account",
            "list",
            "--output",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=CLI_TIMEOUT_SECONDS)
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

        accounts.append(
            DiscoveredAccount(
                provider="azure",
                profile_id=sub_id,
                display_name=f"Azure ({name})",
                env_vars={
                    "AZURE_SUBSCRIPTION_ID": sub_id,
                    "AZURE_TENANT_ID": sub.get("tenantId", ""),
                },
                credentials_valid=True,
            )
        )

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
            "gcloud",
            "projects",
            "list",
            "--format=json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=CLI_TIMEOUT_SECONDS)
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

        accounts.append(
            DiscoveredAccount(
                provider="gcp",
                profile_id=project_id,
                display_name=f"GCP ({name})",
                env_vars={
                    "GCLOUD_PROJECT": project_id,
                },
                credentials_valid=True,
            )
        )

    log.info("discovery.gcp.found", count=len(accounts))
    return accounts


# ---------------------------------------------------------------------------
# Kubernetes
# ---------------------------------------------------------------------------


async def discover_kubernetes_contexts(
    exclude_contexts: list[str] | None = None,
    kubeconfig_path: str | None = None,
) -> list[DiscoveredAccount]:
    """Parse kubeconfig to discover cluster contexts.

    Uses `kubectl config get-contexts -o name` so we honour KUBECONFIG env,
    merged configs, and the user's preferred resolution rules without
    re-implementing them. Returns empty when kubectl is missing or the user
    has no contexts.
    """
    exclude = set(exclude_contexts or [])
    kubectl = shutil.which("kubectl")
    if not kubectl:
        log.info("discovery.kubernetes.cli_not_found")
        return []

    env: dict[str, str] | None = None
    if kubeconfig_path:
        env = {"KUBECONFIG": kubeconfig_path}

    try:
        proc = await asyncio.create_subprocess_exec(
            kubectl,
            "config",
            "get-contexts",
            "-o",
            "name",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **(env or {})},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=CLI_TIMEOUT_SECONDS)
    except TimeoutError:
        log.warning("discovery.kubernetes.timeout")
        return []
    except Exception as exc:
        log.warning("discovery.kubernetes.error", error=str(exc))
        return []

    if proc.returncode != 0:
        log.warning("discovery.kubernetes.cli_failed", stderr=stderr.decode()[:200])
        return []

    accounts: list[DiscoveredAccount] = []
    for line in stdout.decode().splitlines():
        ctx = line.strip()
        if not ctx or ctx in exclude:
            continue
        accounts.append(
            DiscoveredAccount(
                provider="kubernetes",
                profile_id=ctx,
                display_name=f"Kubernetes ({ctx})",
                env_vars={
                    "KUBECONFIG": kubeconfig_path or os.environ.get("KUBECONFIG", ""),
                    "KUBE_CONTEXT": ctx,
                },
                credentials_valid=True,
            )
        )

    log.info("discovery.kubernetes.found", count=len(accounts))
    return accounts


# ---------------------------------------------------------------------------
# Cloudflare (single backend, env-token auth)
# ---------------------------------------------------------------------------


def cloudflare_account_if_configured(token_env: str) -> DiscoveredAccount | None:
    """Return a single discovered Cloudflare 'account' if its token is set.

    No API call here: we just check that the env var exists. The MCP backend
    will validate the token when it starts.
    """
    token = os.environ.get(token_env, "").strip()
    if not token:
        log.info("discovery.cloudflare.no_token", env=token_env)
        return None
    return DiscoveredAccount(
        provider="cloudflare",
        profile_id="default",
        display_name="Cloudflare",
        env_vars={token_env: token},
        credentials_valid=True,
    )


# ---------------------------------------------------------------------------
# DigitalOcean (single backend, env-token auth)
# ---------------------------------------------------------------------------


def digitalocean_account_if_configured(token_env: str) -> DiscoveredAccount | None:
    """Return a single discovered DigitalOcean 'account' if its token is set."""
    token = os.environ.get(token_env, "").strip()
    if not token:
        log.info("discovery.digitalocean.no_token", env=token_env)
        return None
    return DiscoveredAccount(
        provider="digitalocean",
        profile_id="default",
        display_name="DigitalOcean",
        env_vars={token_env: token},
        credentials_valid=True,
    )


# ---------------------------------------------------------------------------
# Azure DevOps (per-org backends)
# ---------------------------------------------------------------------------


def azure_devops_accounts(organizations: list[str]) -> list[DiscoveredAccount]:
    """Each configured organization becomes one DiscoveredAccount.

    Authentication happens inside the ADO MCP server itself (it uses the
    ``az`` CLI's existing login). We pass the org name through env so the
    server knows which to target.
    """
    accounts: list[DiscoveredAccount] = []
    for org in organizations:
        org = org.strip()
        if not org:
            continue
        accounts.append(
            DiscoveredAccount(
                provider="azure_devops",
                profile_id=org,
                display_name=f"Azure DevOps ({org})",
                env_vars={"AZURE_DEVOPS_ORG": org},
                credentials_valid=True,
            )
        )
    return accounts


# ---------------------------------------------------------------------------
# Playwright (single backend, no auth)
# ---------------------------------------------------------------------------


def playwright_account() -> DiscoveredAccount:
    """Single Playwright backend, always 'available'.

    The Playwright MCP server runs a real browser locally and has no concept
    of accounts; it just exposes browser-interaction tools.
    """
    return DiscoveredAccount(
        provider="playwright",
        profile_id="default",
        display_name="Playwright (browser)",
        env_vars={},
        credentials_valid=True,
    )


# ---------------------------------------------------------------------------
# Remote MCP integrations
# ---------------------------------------------------------------------------


def github_remote_account_if_configured(url: str, token_env: str) -> DiscoveredAccount | None:
    """GitHub's hosted MCP server. Needs a personal access token."""
    token = os.environ.get(token_env, "").strip()
    if not token:
        log.info("discovery.github_remote.no_token", env=token_env)
        return None
    return DiscoveredAccount(
        provider="github_remote",
        profile_id="default",
        display_name="GitHub (remote)",
        env_vars={
            "_REMOTE_URL": url,
            "_REMOTE_HEADER_Authorization": f"Bearer {token}",
        },
        credentials_valid=True,
    )


def microsoft_learn_account(url: str) -> DiscoveredAccount:
    """Anonymous Microsoft Learn MCP server."""
    return DiscoveredAccount(
        provider="microsoft_learn",
        profile_id="default",
        display_name="Microsoft Learn (remote)",
        env_vars={"_REMOTE_URL": url},
        credentials_valid=True,
    )


def aws_knowledge_account(url: str) -> DiscoveredAccount:
    """Anonymous AWS Knowledge MCP server."""
    return DiscoveredAccount(
        provider="aws_knowledge",
        profile_id="default",
        display_name="AWS Knowledge (remote)",
        env_vars={"_REMOTE_URL": url},
        credentials_valid=True,
    )


def aws_managed_account(url: str, token_env: str | None) -> DiscoveredAccount:
    """AWS's general-purpose managed MCP proxy. Anonymous unless token_env set."""
    env_vars: dict[str, str] = {"_REMOTE_URL": url}
    if token_env:
        token = os.environ.get(token_env, "").strip()
        if token:
            env_vars["_REMOTE_HEADER_Authorization"] = f"Bearer {token}"
    return DiscoveredAccount(
        provider="aws_managed",
        profile_id="default",
        display_name="AWS (managed remote MCP)",
        env_vars=env_vars,
        credentials_valid=True,
    )


async def _fetch_gcp_token(token_env: str | None, token_command: list[str]) -> str | None:
    """Mint a GCP access token. Prefer env var; else run the configured command."""
    if token_env:
        env_token = os.environ.get(token_env, "").strip()
        if env_token:
            return env_token
    if not token_command:
        return None
    cmd = shutil.which(token_command[0]) or token_command[0]
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd,
            *token_command[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except Exception as exc:
        log.warning("discovery.gcp_remote.token_command_failed", error=str(exc))
        return None
    if proc.returncode != 0:
        return None
    token = stdout.decode().strip()
    return token or None


async def gcp_remote_accounts(
    services: Sequence[object],
    token_env: str | None,
    token_command: list[str],
) -> list[DiscoveredAccount]:
    """One DiscoveredAccount per configured GCP managed-MCP service.

    Auth strategy depends on which option is set:

    - ``token_env``: stamp the env-var value as a static Bearer header once.
      Suitable for long-lived tokens / service-account access tokens.
    - ``token_command``: install a ``CommandTokenAuth`` flow on each backend
      so the token is minted at start and re-minted on any 401 — short-lived
      ADC tokens rotate cleanly without restarting the backend.

    If both are present, token_env wins (predictable for tests).
    """
    if not services:
        return []
    accounts: list[DiscoveredAccount] = []
    static_token: str | None = None
    if token_env:
        env_value = os.environ.get(token_env, "").strip()
        if env_value:
            static_token = env_value
    if static_token is None and not token_command:
        # Validate that gcloud (or whatever command) is at least runnable.
        # If not, fall back to a one-shot mint so the user gets a clear
        # error early instead of a 401 storm later.
        log.warning("discovery.gcp_remote.no_auth_configured")
        return []

    for svc in services:
        name = getattr(svc, "name", "")
        url = getattr(svc, "url", "")
        if not name or not url:
            continue
        env_vars: dict[str, str] = {"_REMOTE_URL": url}
        if static_token is not None:
            env_vars["_REMOTE_HEADER_Authorization"] = f"Bearer {static_token}"
        else:
            # Signal to expand_backends that this account uses the rotating
            # auth flow with the configured command.
            env_vars["_REMOTE_AUTH_COMMAND"] = " ".join(token_command)
        accounts.append(
            DiscoveredAccount(
                provider="gcp_remote",
                profile_id=name,
                display_name=f"GCP managed: {name}",
                env_vars=env_vars,
                credentials_valid=True,
            )
        )
    log.info("discovery.gcp_remote.found", count=len(accounts), refresh=static_token is None)
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

    tasks: list[tuple[str, asyncio.Task[list[DiscoveredAccount]]]] = []
    if config.azure.enabled:
        enabled_providers.append("azure")
        tasks.append(
            (
                "azure",
                asyncio.create_task(
                    discover_azure_subscriptions(
                        config.azure.exclude_subscriptions,
                        validate_credentials=require_credentials,
                    )
                ),
            )
        )
    if config.gcp.enabled:
        enabled_providers.append("gcp")
        tasks.append(
            (
                "gcp",
                asyncio.create_task(
                    discover_gcp_projects(
                        config.gcp.exclude_projects,
                        validate_credentials=require_credentials,
                    )
                ),
            )
        )
    if config.kubernetes.enabled:
        enabled_providers.append("kubernetes")
        tasks.append(
            (
                "kubernetes",
                asyncio.create_task(
                    discover_kubernetes_contexts(
                        exclude_contexts=config.kubernetes.exclude_contexts,
                        kubeconfig_path=config.kubernetes.kubeconfig_path,
                    )
                ),
            )
        )

    # Token-gated providers don't need their own task — env-var check is cheap.
    if config.cloudflare.enabled:
        enabled_providers.append("cloudflare")
        cf = cloudflare_account_if_configured(config.cloudflare.token_env)
        if cf is not None:
            all_accounts.append(cf)
        else:
            failed_providers.append("cloudflare")
    if config.digitalocean.enabled:
        enabled_providers.append("digitalocean")
        do = digitalocean_account_if_configured(config.digitalocean.token_env)
        if do is not None:
            all_accounts.append(do)
        else:
            failed_providers.append("digitalocean")

    if config.azure_devops.enabled and config.azure_devops.organizations:
        enabled_providers.append("azure_devops")
        ado_accounts = azure_devops_accounts(config.azure_devops.organizations)
        all_accounts.extend(ado_accounts)

    if config.playwright.enabled:
        enabled_providers.append("playwright")
        all_accounts.append(playwright_account())

    # Remote MCP integrations.
    if config.github_remote.enabled:
        enabled_providers.append("github_remote")
        gh = github_remote_account_if_configured(
            config.github_remote.url, config.github_remote.token_env
        )
        if gh is not None:
            all_accounts.append(gh)
        else:
            failed_providers.append("github_remote")
    if config.microsoft_learn.enabled:
        enabled_providers.append("microsoft_learn")
        all_accounts.append(microsoft_learn_account(config.microsoft_learn.url))
    if config.aws_knowledge.enabled:
        enabled_providers.append("aws_knowledge")
        all_accounts.append(aws_knowledge_account(config.aws_knowledge.url))
    if config.aws_managed.enabled:
        enabled_providers.append("aws_managed")
        all_accounts.append(
            aws_managed_account(config.aws_managed.url, config.aws_managed.token_env)
        )
    if config.gcp_remote.enabled and config.gcp_remote.services:
        enabled_providers.append("gcp_remote")
        gcp_remote = await gcp_remote_accounts(
            config.gcp_remote.services,
            config.gcp_remote.token_env,
            config.gcp_remote.token_command,
        )
        all_accounts.extend(gcp_remote)

    # Third-party plugin providers (entry-point registered).
    from cloud_engineer_mcp.plugins import discover_from_plugins, iter_loaded_plugins

    plugin_count = sum(1 for _ in iter_loaded_plugins())
    if plugin_count:
        enabled_providers.append(f"plugins[{plugin_count}]")
        try:
            plugin_accounts = await discover_from_plugins()
            all_accounts.extend(plugin_accounts)
        except Exception as exc:
            log.warning("discovery.plugins.unexpected_error", error=str(exc))

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
        if "kubernetes" in failed_providers:
            hints.append("Kubernetes: install kubectl and configure ~/.kube/config")
        if "cloudflare" in failed_providers:
            hints.append("Cloudflare: set CLOUDFLARE_API_TOKEN in the environment")
        if "digitalocean" in failed_providers:
            hints.append("DigitalOcean: set DIGITALOCEAN_TOKEN in the environment")
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


def _aws_server_tag(mcp_server: str) -> str:
    """Distill an awslabs server spec into a 12-char tag for backend IDs.

    Examples:
        awslabs.lambda-tool-mcp-server@latest -> "lambda"
        awslabs.dynamodb-mcp-server          -> "dynamodb"
        awslabs.bedrock-kb-retrieval-mcp-server@latest -> "bedrock_kb"
    """
    name = mcp_server.split("@", 1)[0]
    name = name.removeprefix("awslabs.").removesuffix("-mcp-server")
    # Drop a few well-known noise suffixes so the tags read cleanly.
    for noisy in ("-tool", "-retrieval", "-anonymous"):
        name = name.removesuffix(noisy)
    return _safe_id(name, max_len=12)


def _resolve_npm_command(
    package: str,
    extra_args: list[str] | None = None,
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
            base_env = {
                **account.env_vars,
                "FASTMCP_LOG_LEVEL": "WARNING",
            }
            # The primary AWS server (typically aws-iac), one per profile.
            primary_id = f"aws_{_safe_id(account.profile_id)}"
            command, args = _resolve_aws_command(aws_cfg.mcp_server)
            backends[primary_id] = BackendConfig(
                display_name=account.display_name,
                command=command,
                args=args,
                env=base_env,
                enabled=True,
                restart_on_failure=True,
                max_restarts=3,
                startup_timeout_seconds=aws_cfg.startup_timeout_seconds,
                health_check_interval_seconds=60,
            )
            # Any extra specialized AWS Labs servers configured by the user.
            for extra_server in aws_cfg.extra_servers:
                spec = extra_server.strip()
                if not spec:
                    continue
                tag = _aws_server_tag(spec)
                extra_id = f"aws_{_safe_id(account.profile_id, max_len=6)}_{tag}"
                cmd, extra_args = _resolve_aws_command(spec)
                backends[extra_id] = BackendConfig(
                    display_name=f"{account.display_name} — {tag}",
                    command=cmd,
                    args=extra_args,
                    env=base_env,
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

        elif account.provider == "kubernetes":
            k8s_cfg = config.kubernetes
            backend_id = f"k8s_{_safe_id(account.profile_id, max_len=12)}"
            mcp_parts = k8s_cfg.mcp_command.split()
            pkg = next((p for p in mcp_parts if not p.startswith(("npx", "-y"))), "")
            command, args = _resolve_npm_command(pkg)
            backends[backend_id] = BackendConfig(
                display_name=account.display_name,
                command=command,
                args=args,
                env=account.env_vars,
                enabled=True,
                restart_on_failure=True,
                max_restarts=3,
                startup_timeout_seconds=k8s_cfg.startup_timeout_seconds,
                health_check_interval_seconds=60,
            )

        elif account.provider == "cloudflare":
            cf_cfg = config.cloudflare
            mcp_parts = cf_cfg.mcp_command.split()
            pkg = next((p for p in mcp_parts if not p.startswith(("npx", "-y"))), "")
            command, args = _resolve_npm_command(pkg)
            backends["cloudflare"] = BackendConfig(
                display_name=account.display_name,
                command=command,
                args=args,
                env=account.env_vars,
                enabled=True,
                restart_on_failure=True,
                max_restarts=3,
                startup_timeout_seconds=cf_cfg.startup_timeout_seconds,
                health_check_interval_seconds=60,
            )

        elif account.provider == "digitalocean":
            do_cfg = config.digitalocean
            mcp_parts = do_cfg.mcp_command.split()
            pkg = next((p for p in mcp_parts if not p.startswith(("npx", "-y"))), "")
            command, args = _resolve_npm_command(pkg)
            backends["digitalocean"] = BackendConfig(
                display_name=account.display_name,
                command=command,
                args=args,
                env=account.env_vars,
                enabled=True,
                restart_on_failure=True,
                max_restarts=3,
                startup_timeout_seconds=do_cfg.startup_timeout_seconds,
                health_check_interval_seconds=60,
            )

        elif account.provider == "azure_devops":
            ado_cfg = config.azure_devops
            backend_id = f"ado_{_safe_id(account.profile_id, max_len=12)}"
            mcp_parts = ado_cfg.mcp_command.split()
            pkg = next((p for p in mcp_parts if not p.startswith(("npx", "-y"))), "")
            command, args = _resolve_npm_command(pkg)
            backends[backend_id] = BackendConfig(
                display_name=account.display_name,
                command=command,
                args=args,
                env=account.env_vars,
                enabled=True,
                restart_on_failure=True,
                max_restarts=3,
                startup_timeout_seconds=ado_cfg.startup_timeout_seconds,
                health_check_interval_seconds=60,
            )

        elif account.provider == "playwright":
            pw_cfg = config.playwright
            mcp_parts = pw_cfg.mcp_command.split()
            pkg = next((p for p in mcp_parts if not p.startswith(("npx", "-y"))), "")
            command, args = _resolve_npm_command(pkg)
            backends["playwright"] = BackendConfig(
                display_name=account.display_name,
                command=command,
                args=args,
                env=account.env_vars,
                enabled=True,
                restart_on_failure=True,
                max_restarts=3,
                startup_timeout_seconds=pw_cfg.startup_timeout_seconds,
                health_check_interval_seconds=60,
            )

        elif account.provider in {
            "github_remote",
            "microsoft_learn",
            "aws_knowledge",
            "aws_managed",
            "gcp_remote",
        }:
            # Remote MCP — stamp URL + headers from the env_vars convention
            # used by discovery (keys prefixed with `_REMOTE_HEADER_`).
            url = account.env_vars.get("_REMOTE_URL", "")
            headers: dict[str, str] = {}
            for k, v in account.env_vars.items():
                if k.startswith("_REMOTE_HEADER_"):
                    headers[k.removeprefix("_REMOTE_HEADER_")] = v
            auth_cmd_str = account.env_vars.get("_REMOTE_AUTH_COMMAND", "").strip()
            auth_cmd = auth_cmd_str.split() if auth_cmd_str else []
            prefix = {
                "github_remote": "gh_remote",
                "microsoft_learn": "mslearn",
                "aws_knowledge": "aws_kb",
                "aws_managed": "aws_rem",
                "gcp_remote": "gcp_rem",
            }[account.provider]
            backend_id = f"{prefix}_{_safe_id(account.profile_id, max_len=10)}"
            backends[backend_id] = BackendConfig(
                display_name=account.display_name,
                transport="http",
                url=url,
                headers=headers,
                auth_refresh_command=auth_cmd,
                enabled=True,
                restart_on_failure=True,
                max_restarts=3,
                startup_timeout_seconds=30,
                health_check_interval_seconds=120,
            )

    # Plugin-sourced accounts are kept in a separate bucket; expand them via
    # the plugin's own expand() rather than the built-in branches above.
    _builtin_providers = {
        "aws",
        "azure",
        "gcp",
        "kubernetes",
        "cloudflare",
        "digitalocean",
        "azure_devops",
        "playwright",
        "github_remote",
        "microsoft_learn",
        "aws_knowledge",
        "aws_managed",
        "gcp_remote",
    }
    plugin_accounts = [
        a for a in discovered if a.credentials_valid and a.provider not in _builtin_providers
    ]
    if plugin_accounts:
        from cloud_engineer_mcp.plugins import expand_from_plugins

        backends.update(expand_from_plugins(plugin_accounts))

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
