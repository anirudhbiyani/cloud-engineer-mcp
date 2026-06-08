"""CLI entrypoint for cloud_engineer_mcp."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

import click

from cloud_engineer_mcp import __version__
from cloud_engineer_mcp.config import CloudEngineerConfig
from cloud_engineer_mcp.errors import ConfigError
from cloud_engineer_mcp.observability.logging import configure_logging, get_logger


@click.group()
@click.version_option(__version__, prog_name="cloud-engineer-mcp")
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase log verbosity. -v=INFO, -vv=DEBUG. Overrides config and LOG_LEVEL env.",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    help="Quiet mode — only ERROR-level logs.",
)
@click.pass_context
def cli(ctx: click.Context, verbose: int, quiet: bool) -> None:
    """cloud-engineer-mcp - One MCP endpoint for AWS, Azure, and GCP."""
    if quiet and verbose:
        click.echo("Error: --quiet and --verbose are mutually exclusive.", err=True)
        ctx.exit(2)
    if quiet:
        os.environ["LOG_LEVEL"] = "ERROR"
    elif verbose >= 2:
        os.environ["LOG_LEVEL"] = "DEBUG"
    elif verbose == 1:
        os.environ["LOG_LEVEL"] = "INFO"


@cli.command()
@click.option(
    "--config",
    "-c",
    default="config.yml",
    envvar="CLOUD_ENGINEER_MCP_CONFIG",
    help="Config file path",
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
    from cloud_engineer_mcp.observability.tracing import configure_tracing

    configure_tracing(service_name=cfg.server.name)
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

    log = get_logger("cli")
    _install_shutdown_signal_handlers(log)

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

    except asyncio.CancelledError:
        log.info("cli.shutdown_requested")
    finally:
        # Shield shutdown from further cancellation so SIGTERM during teardown
        # doesn't leave backend subprocesses orphaned.
        await asyncio.shield(_safe_shutdown(components))


async def _safe_shutdown(components: object) -> None:
    try:
        await components.shutdown()  # type: ignore[attr-defined]
    except Exception as exc:
        get_logger("cli").error("cli.shutdown_error", error=str(exc))


def _install_shutdown_signal_handlers(log: object) -> None:
    """Convert SIGINT/SIGTERM into cancellation of the main task.

    asyncio's KeyboardInterrupt path covers Ctrl-C in interactive shells, but
    Docker/Kubernetes/systemd send SIGTERM. Install handlers so both produce
    the same clean-shutdown path.

    On Windows, add_signal_handler raises NotImplementedError; we silently
    fall back to the default behavior (KeyboardInterrupt only).
    """
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    if main_task is None:
        return

    def _request_stop(signame: str) -> None:
        log.info("cli.signal_received", signal=signame)  # type: ignore[attr-defined]
        main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop, sig.name)


@cli.command()
@click.option("--config", "-c", default="config.yml", envvar="CLOUD_ENGINEER_MCP_CONFIG")
def check(config: str) -> None:
    """Validate configuration and check backend connectivity."""
    cfg = _load_config(config)
    configure_logging(_resolve_log_level(cfg), "console")

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
    configure_logging(_resolve_log_level(cfg), "console")
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
    configure_logging(_resolve_log_level(cfg), "console")
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
    configure_logging(_resolve_log_level(cfg), "console")

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
    "--config",
    "-c",
    default="config.yml",
    envvar="CLOUD_ENGINEER_MCP_CONFIG",
    help="Config file path",
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

    from typing import Any

    existing: dict[str, Any] = {}
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


@cli.command("plugins")
def plugins_cmd() -> None:
    """List installed backend plugins."""
    from cloud_engineer_mcp.plugins import iter_loaded_plugins

    plugins = list(iter_loaded_plugins())
    if not plugins:
        click.echo("No backend plugins installed.")
        click.echo(
            "\nThird-party backend plugins register via the entry-point group\n"
            "  'cloud_engineer_mcp.backend_providers'.\n"
            "See docs/PLUGINS.md for the authoring guide."
        )
        return

    click.echo(f"\nInstalled backend plugins ({len(plugins)}):\n")
    for p in plugins:
        click.echo(f"  {p.name}")
        click.echo(f"    package: {p.distribution} v{p.version}")
        click.echo(f"    provider: {type(p.provider).__module__}.{type(p.provider).__name__}")
        click.echo()


def _resolve_log_level(cfg: CloudEngineerConfig) -> str:
    """Resolve effective log level. Env var (set by -v/-q) wins over config."""
    return os.environ.get("LOG_LEVEL") or cfg.logging.level


@cli.command("eval")
@click.option(
    "--threshold",
    default=0.85,
    show_default=True,
    type=float,
    help="Minimum Recall@15 for the eval to pass (CI gate). Use 0 to never fail.",
)
@click.option(
    "--keyword-only",
    is_flag=True,
    help="Use the embedding backend's keyword fallback. Compat with old flag.",
)
@click.option(
    "--backend",
    type=click.Choice(["embedding", "bm25"]),
    default=None,
    help="Which selector backend to eval. Overrides --keyword-only.",
)
@click.option(
    "--show-misses/--no-show-misses",
    default=True,
    show_default=True,
    help="Print which eval cases missed top-K.",
)
def eval_cmd(threshold: float, keyword_only: bool, backend: str | None, show_misses: bool) -> None:
    """Run the selector eval harness and print Recall@K.

    Use this to gate selector changes in CI:

        cloud-engineer-mcp eval --threshold 0.85

    Exit code is non-zero when Recall@15 is below `--threshold`.
    """
    from cloud_engineer_mcp.eval import run_eval

    click.echo("Running selector eval...")
    result = run_eval(
        use_embeddings=not keyword_only,
        backend=backend,
    )

    click.echo(f"\nMode:           {result.mode}")
    click.echo(f"Catalog size:   {result.catalog_size}")
    click.echo(f"Eval cases:     {result.total_cases}")
    click.echo(f"Search p99:     {result.p99_latency_ms:.2f}ms\n")

    for k in sorted(result.recall_at):
        pct = result.recall_at[k] * 100
        click.echo(f"  Recall@{k:<3}     {pct:.1f}%")

    click.echo(f"\nMean rank:      {result.mean_rank:.2f}")
    click.echo(f"Median rank:    {result.median_rank:.1f}")
    click.echo(f"Misses:         {len(result.misses)} / {result.total_cases}")

    if show_misses and result.misses:
        click.echo("\nMissed cases (not in top-30):")
        for miss in result.misses:
            click.echo(f"  - {miss}")

    if not result.passed(threshold):
        click.echo(
            f"\nFAIL: Recall@15 = {result.recall_at.get(15, 0.0):.3f} < threshold {threshold}",
            err=True,
        )
        sys.exit(1)
    click.echo(f"\nPASS: Recall@15 = {result.recall_at.get(15, 0.0):.3f}")


def _load_config(path: str) -> CloudEngineerConfig:
    config_path = Path(path)
    if not config_path.exists():
        click.echo(f"Error: Config file not found: {path}", err=True)
        sys.exit(1)
    try:
        return CloudEngineerConfig.from_yaml(config_path)
    except Exception as exc:
        raise ConfigError(f"Failed to load config: {exc}") from exc


@cli.command("demo")
@click.option(
    "--transport",
    "-t",
    type=click.Choice(["stdio", "http", "both"]),
    default="stdio",
    show_default=True,
    help="Transport to expose. stdio for IDE integration, http for browser/curl.",
)
@click.option("--host", default="127.0.0.1", show_default=True, help="HTTP bind host.")
@click.option("--port", default=8080, show_default=True, type=int, help="HTTP port.")
def demo(transport: str, host: str, port: int) -> None:
    """Start the gateway with bundled mock backends — no cloud credentials needed.

    The mock backends expose realistic-looking AWS/Azure/GCP tools so you can
    exercise the gateway end-to-end (set_context, tools/list, tool routing) in
    under a minute. Useful for evaluating the project, IDE setup walkthroughs,
    and conference rehearsals.
    """
    cfg = _build_demo_config(host=host, port=port)
    configure_logging(_resolve_log_level(cfg), cfg.logging.format)
    from cloud_engineer_mcp.observability.tracing import configure_tracing

    configure_tracing(service_name=cfg.server.name)
    log = get_logger("cli")
    log.info("cli.demo", transport=transport)

    click.echo("cloud-engineer-mcp demo: mock backends, no cloud credentials used.")
    if transport in {"http", "both"}:
        click.echo(f"  HTTP gateway: http://{host}:{port}/mcp")
    click.echo("  Mock backends: aws-demo, azure-demo, gcp-demo")
    click.echo("  Try: set_context('list my S3 buckets')\n")

    asyncio.run(_run_server(cfg, transport))


def _build_demo_config(host: str, port: int) -> CloudEngineerConfig:
    """Construct an in-memory config wired to bundled mock backends.

    Skips cloud discovery entirely. One mock backend per cloud provider so the
    namespace/provider filtering behavior is observable.
    """
    from cloud_engineer_mcp.config import (
        AWSDiscoveryConfig,
        AzureDiscoveryConfig,
        BackendConfig,
        DiscoveryConfig,
        GCPDiscoveryConfig,
        HttpTransportConfig,
        ServerConfig,
        TransportConfig,
    )

    demo_module = "cloud_engineer_mcp._demo_backend"

    def _backend(display: str) -> BackendConfig:
        return BackendConfig(
            display_name=display,
            command=sys.executable,
            args=["-m", demo_module],
            enabled=True,
            startup_timeout_seconds=30,
        )

    return CloudEngineerConfig(
        server=ServerConfig(
            transports=TransportConfig(
                http=HttpTransportConfig(host=host, port=port, cors_origins=[]),
            ),
        ),
        discovery=DiscoveryConfig(
            enabled=False,
            aws=AWSDiscoveryConfig(enabled=False),
            azure=AzureDiscoveryConfig(enabled=False),
            gcp=GCPDiscoveryConfig(enabled=False),
        ),
        backends={
            "aws_demo": _backend("AWS (demo)"),
            "az_demo": _backend("Azure (demo)"),
            "gcp_demo": _backend("GCP (demo)"),
        },
    )
