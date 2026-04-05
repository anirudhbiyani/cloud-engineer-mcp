"""CLI entrypoint for cloud_engineer_mcp."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

import click

from cloud_engineer_mcp.config import CloudEngineerConfig
from cloud_engineer_mcp.errors import ConfigError
from cloud_engineer_mcp.observability.logging import configure_logging, get_logger


@click.group()
def cli() -> None:
    """cloud-engineer-mcp - Unified Multi-Cloud MCP Gateway Server"""


@cli.command()
@click.option(
    "--config", "-c", default="config.yml",
    envvar="CLOUD_ENGINEER_MCP_CONFIG", help="Config file path",
)
@click.option(
    "--transport",
    "-t",
    type=click.Choice(["stdio", "http", "both"]),
    default="both",
)
@click.option("--host", default=None, help="Override HTTP host")
@click.option("--port", default=None, type=int, help="Override HTTP port")
@click.option(
    "--log-level",
    default=None,
    envvar="LOG_LEVEL",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
)
def serve(
    config: str,
    transport: str,
    host: str | None,
    port: int | None,
    log_level: str | None,
) -> None:
    """Start the cloud-engineer-mcp gateway server."""
    cfg = _load_config(config)

    if log_level:
        cfg.logging.level = log_level
    if host:
        cfg.server.transports.http.host = host
    if port:
        cfg.server.transports.http.port = port

    configure_logging(cfg.logging.level, cfg.logging.format, cfg.logging.file)
    log = get_logger("cli")
    log.info("cli.serve", config=config, transport=transport)

    asyncio.run(_run_server(cfg, transport))


async def _run_server(cfg: CloudEngineerConfig, transport: str) -> None:
    from cloud_engineer_mcp.errors import CredentialError
    from cloud_engineer_mcp.server import GatewayComponents, create_server

    try:
        components = await GatewayComponents.create(cfg)
    except CredentialError as exc:
        log = get_logger("cli")
        log.error("startup.credential_error", error=str(exc))
        click.echo(f"\nError: {exc}", err=True)
        click.echo("Fix your cloud credentials and restart.", err=True)
        sys.exit(1)

    server, components = create_server(cfg, components)
    await components.startup()

    try:
        if transport == "stdio":
            from cloud_engineer_mcp.transport.stdio import run_stdio

            await run_stdio(server, cfg)

        elif transport == "http":
            from cloud_engineer_mcp.observability.health import HealthCheck
            from cloud_engineer_mcp.observability.metrics import metrics_endpoint
            from cloud_engineer_mcp.transport.http import run_http

            health = HealthCheck(
                cfg,
                components.backend_manager,
                components.tool_index,
                components.session_manager,
                components.start_time,
            )
            await run_http(
                server,
                cfg,
                health_handler=health,
                metrics_handler=metrics_endpoint,
            )

        elif transport == "both":
            from cloud_engineer_mcp.observability.health import HealthCheck
            from cloud_engineer_mcp.observability.metrics import metrics_endpoint
            from cloud_engineer_mcp.transport.http import run_http
            from cloud_engineer_mcp.transport.stdio import run_stdio

            health = HealthCheck(
                cfg,
                components.backend_manager,
                components.tool_index,
                components.session_manager,
                components.start_time,
            )
            http_task = asyncio.create_task(
                run_http(
                    server,
                    cfg,
                    health_handler=health,
                    metrics_handler=metrics_endpoint,
                )
            )
            try:
                await run_stdio(server, cfg)
            finally:
                http_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await http_task

    finally:
        await components.shutdown()


@cli.command()
@click.option("--config", "-c", default="config.yml", envvar="CLOUD_ENGINEER_MCP_CONFIG")
def check(config: str) -> None:
    """Validate configuration and check backend connectivity."""
    cfg = _load_config(config)
    configure_logging(cfg.logging.level, "console")

    click.echo(f"Configuration valid: {config}")
    click.echo(f"  Server: {cfg.server.name} v{cfg.server.version}")
    click.echo(f"  Selector model: {cfg.selector.model_name}")
    click.echo(f"  Backends ({len(cfg.backends)}):")
    for bid, bcfg in cfg.backends.items():
        status = "enabled" if bcfg.enabled else "disabled"
        click.echo(f"    - {bid}: {bcfg.display_name} [{status}]")
        click.echo(f"      command: {bcfg.command} {' '.join(bcfg.args)}")


@cli.command("list-tools")
@click.option("--config", "-c", default="config.yml", envvar="CLOUD_ENGINEER_MCP_CONFIG")
def list_tools(config: str) -> None:
    """Start backends and list all discovered tools."""
    cfg = _load_config(config)
    configure_logging(cfg.logging.level, "console")
    asyncio.run(_list_tools(cfg))


async def _list_tools(cfg: CloudEngineerConfig) -> None:
    from cloud_engineer_mcp.backends.manager import BackendManager
    from cloud_engineer_mcp.discovery import discover_all, expand_backends

    discovered = await discover_all(cfg.discovery)
    all_backends = expand_backends(discovered, cfg.discovery, cfg.backends)

    mgr = BackendManager.from_config(all_backends)
    await mgr.start_all()

    click.echo(f"\nDiscovered tools ({mgr.registry.tool_count} total):\n")
    for ref in mgr.registry.all_refs():
        click.echo(f"  {ref.namespaced_name}")
        if ref.tool.description:
            click.echo(f"    {ref.tool.description[:80]}")
        click.echo()

    await mgr.stop_all()


@cli.command("discover")
@click.option("--config", "-c", default="config.yml", envvar="CLOUD_ENGINEER_MCP_CONFIG")
def discover(config: str) -> None:
    """Preview cloud accounts that would be auto-discovered."""
    cfg = _load_config(config)
    configure_logging(cfg.logging.level, "console")
    asyncio.run(_discover(cfg))


async def _discover(cfg: CloudEngineerConfig) -> None:
    from cloud_engineer_mcp.discovery import discover_all, expand_backends
    from cloud_engineer_mcp.errors import CredentialError

    if not cfg.discovery.enabled:
        click.echo("Discovery is disabled in config.")
        return

    try:
        discovered = await discover_all(cfg.discovery, require_credentials=True)
    except CredentialError as exc:
        click.echo(f"\nError: {exc}", err=True)
        click.echo("\nFix your credentials and try again.", err=True)
        sys.exit(1)

    if not discovered:
        click.echo("No cloud accounts discovered.")
        return

    valid = [a for a in discovered if a.credentials_valid]
    invalid = [a for a in discovered if not a.credentials_valid]

    count, valid_count = len(discovered), len(valid)
    click.echo(f"\nDiscovered {count} cloud account(s) ({valid_count} with valid credentials):\n")
    for acct in discovered:
        cred_status = "VALID" if acct.credentials_valid else "INVALID"
        click.echo(f"  [{acct.provider.upper()}] {acct.display_name}  [{cred_status}]")
        click.echo(f"    profile_id: {acct.profile_id}")
        for k, v in acct.env_vars.items():
            click.echo(f"    {k}={v}")
        click.echo()

    if invalid:
        click.echo(f"  {len(invalid)} account(s) skipped due to invalid credentials.\n")

    all_backends = expand_backends(discovered, cfg.discovery, cfg.backends)
    click.echo(f"Would generate {len(all_backends)} backend(s):\n")
    for bid, bcfg in all_backends.items():
        status = "enabled" if bcfg.enabled else "disabled"
        click.echo(f"  {bid}: {bcfg.display_name} [{status}]")
        click.echo(f"    command: {bcfg.command} {' '.join(bcfg.args)}")
        click.echo()


@cli.command("install-backends")
@click.option("--config", "-c", default="config.yml", envvar="CLOUD_ENGINEER_MCP_CONFIG")
def install_backends(config: str) -> None:
    """Pre-install backend MCP server packages for fast startup.

    Downloads and installs AWS (via uv), Azure and GCP (via npm)
    backend packages locally so they don't need to be re-downloaded
    on every server start.
    """
    cfg = _load_config(config)
    configure_logging(cfg.logging.level, "console")

    from cloud_engineer_mcp.installer import install_all_backends

    click.echo("Installing backend MCP server packages...\n")
    results = install_all_backends(
        aws_package=cfg.discovery.aws.mcp_server,
        azure_command=cfg.discovery.azure.mcp_command,
        gcp_package=cfg.discovery.gcp.mcp_server,
        aws_enabled=cfg.discovery.aws.enabled,
        azure_enabled=cfg.discovery.azure.enabled,
        gcp_enabled=cfg.discovery.gcp.enabled,
    )

    if results:
        click.echo(f"\nInstalled {len(results)} package(s):\n")
        for pkg, binary in results.items():
            click.echo(f"  {pkg}")
            click.echo(f"    binary: {binary}")
            click.echo()
        click.echo("Backends will now start instantly without downloading.")
    else:
        click.echo("\nNo packages were installed. Check the errors above.")


@cli.command("cursor-install")
@click.option(
    "--config", "-c", default="config.yml",
    envvar="CLOUD_ENGINEER_MCP_CONFIG", help="Config file path",
)
@click.option(
    "--project-dir",
    "-d",
    default=".",
    help="Project directory to install into (creates .cursor/mcp.json)",
)
@click.option("--global", "global_", is_flag=True, help="Install globally in ~/.cursor/mcp.json")
def cursor_install(config: str, project_dir: str, global_: bool) -> None:
    """Register cloud-engineer-mcp as an MCP server in Cursor IDE."""
    import json

    python_path = sys.executable
    config_path = str(Path(config).resolve())

    mcp_entry = {
        "command": python_path,
        "args": [
            "-m",
            "cloud_engineer_mcp",
            "serve",
            "--config",
            config_path,
            "--transport",
            "stdio",
        ],
    }

    if global_:
        mcp_json_path = Path.home() / ".cursor" / "mcp.json"
    else:
        mcp_json_path = Path(project_dir).resolve() / ".cursor" / "mcp.json"

    mcp_json_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if mcp_json_path.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            existing = json.loads(mcp_json_path.read_text())

    if "mcpServers" not in existing:
        existing["mcpServers"] = {}

    existing["mcpServers"]["cloud-engineer-mcp"] = mcp_entry
    mcp_json_path.write_text(json.dumps(existing, indent=2) + "\n")

    click.echo(f"Registered cloud-engineer-mcp in {mcp_json_path}")
    click.echo(f"  Python:  {python_path}")
    click.echo(f"  Config:  {config_path}")
    click.echo()
    click.echo("Restart Cursor (or reload the window) to activate the server.")


def _load_config(path: str) -> CloudEngineerConfig:
    config_path = Path(path)
    if not config_path.exists():
        click.echo(f"Error: Config file not found: {path}", err=True)
        sys.exit(1)
    try:
        return CloudEngineerConfig.from_yaml(config_path)
    except Exception as exc:
        raise ConfigError(f"Failed to load config: {exc}") from exc
