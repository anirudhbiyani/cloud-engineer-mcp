"""Pre-install backend MCP server packages for fast startup."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cloud_engineer_mcp.observability.logging import get_logger

log = get_logger("installer")

INSTALL_DIR = Path.home() / ".cloud-engineer-mcp" / "backends"
MANIFEST_FILE = INSTALL_DIR / "manifest.json"


@dataclass
class InstalledBackend:
    package: str
    version: str
    binary_path: str
    runtime: str  # "uv" or "npm"


def _load_manifest() -> dict[str, dict]:
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_manifest(manifest: dict[str, dict]) -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2) + "\n")


def install_uv_package(package: str) -> str | None:
    """Install a Python MCP server package via `uv tool install`.

    Returns the binary path on success, None on failure.
    """
    uv = shutil.which("uv")
    if not uv:
        log.error("installer.uv_not_found")
        return None

    pkg_name = package.split("@")[0]
    log.info("installer.uv_installing", package=package)
    result = subprocess.run(
        [uv, "tool", "install", "--force", package],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        log.error("installer.uv_install_failed", package=package, stderr=result.stderr[:300])
        return None

    binary = shutil.which(pkg_name)
    if binary:
        log.info("installer.uv_installed", package=package, binary=binary)
        return binary

    local_bin = Path.home() / ".local" / "bin" / pkg_name
    if local_bin.exists():
        log.info("installer.uv_installed", package=package, binary=str(local_bin))
        return str(local_bin)

    log.warning("installer.uv_binary_not_found", package=package)
    return None


def install_npm_package(package: str) -> str | None:
    """Install a Node MCP server package via `npm install -g`.

    Returns the binary path on success, None on failure.
    """
    npm = shutil.which("npm")
    if not npm:
        log.error("installer.npm_not_found")
        return None

    pkg_name = package.split("@")[0].split("/")[-1]
    log.info("installer.npm_installing", package=package)
    result = subprocess.run(
        [npm, "install", "-g", package],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        log.error("installer.npm_install_failed", package=package, stderr=result.stderr[:300])
        return None

    binary = shutil.which(pkg_name)
    if binary:
        log.info("installer.npm_installed", package=package, binary=binary)
        return binary

    log.info("installer.npm_installed_no_binary", package=package)
    return None


def install_all_backends(
    aws_package: str = "awslabs.ccapi-mcp-server",
    azure_command: str = "@azure/mcp@latest",
    gcp_package: str = "@google-cloud/gcloud-mcp@latest",
    aws_enabled: bool = True,
    azure_enabled: bool = True,
    gcp_enabled: bool = True,
) -> dict[str, str]:
    """Install all backend packages and return {package: binary_path}.

    Saves a manifest so subsequent runs know where binaries live.
    """
    manifest = _load_manifest()
    results: dict[str, str] = {}

    if aws_enabled:
        binary = install_uv_package(aws_package)
        if binary:
            manifest[aws_package] = {"binary": binary, "runtime": "uv"}
            results[aws_package] = binary

    if azure_enabled:
        azure_pkg = azure_command.replace("npx -y ", "").replace(" server start", "").strip()
        binary = install_npm_package(azure_pkg)
        if binary:
            manifest[azure_pkg] = {"binary": binary, "runtime": "npm"}
            results[azure_pkg] = binary

    if gcp_enabled:
        binary = install_npm_package(gcp_package)
        if binary:
            manifest[gcp_package] = {"binary": binary, "runtime": "npm"}
            results[gcp_package] = binary

    _save_manifest(manifest)
    return results


def get_installed_binary(package: str) -> str | None:
    """Look up a pre-installed binary path from the manifest."""
    manifest = _load_manifest()
    entry = manifest.get(package)
    if entry:
        binary = entry.get("binary", "")
        if Path(binary).exists():
            return binary
    return None
