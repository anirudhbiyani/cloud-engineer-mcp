"""Tests for config loading and validation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cloud_engineer_mcp.config import BackendConfig, CloudEngineerConfig


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yml"
    with open(p, "w") as f:
        yaml.dump(data, f)
    return p


class TestCloudEngineerConfig:
    def test_defaults(self) -> None:
        cfg = CloudEngineerConfig()
        assert cfg.server.name == "cloud-engineer-mcp"
        assert cfg.selector.top_k == 15
        assert cfg.selector.model_name == "all-MiniLM-L6-v2"
        assert cfg.logging.level == "INFO"
        assert cfg.health.enabled is True

    def test_from_yaml_minimal(self, tmp_path: Path) -> None:
        data = {"server": {"name": "test-server"}}
        path = _write_yaml(tmp_path, data)
        cfg = CloudEngineerConfig.from_yaml(path)
        assert cfg.server.name == "test-server"
        assert cfg.server.version == "1.0.0"
        assert cfg.backends == {}

    def test_from_yaml_with_backends(self, tmp_path: Path) -> None:
        data = {
            "backends": {
                "test_backend": {
                    "display_name": "Test",
                    "command": "echo",
                    "args": ["hello"],
                    "enabled": True,
                }
            }
        }
        path = _write_yaml(tmp_path, data)
        cfg = CloudEngineerConfig.from_yaml(path)
        assert "test_backend" in cfg.backends
        b = cfg.backends["test_backend"]
        assert b.display_name == "Test"
        assert b.command == "echo"
        assert b.max_restarts == 3

    def test_env_var_interpolation(self, tmp_path: Path) -> None:
        os.environ["CLOUD_ENGINEER_TEST_VAR"] = "interpolated_value"
        try:
            data = {
                "backends": {
                    "test": {
                        "display_name": "Test",
                        "command": "echo",
                        "env": {"MY_VAR": "${CLOUD_ENGINEER_TEST_VAR}"},
                    }
                }
            }
            path = _write_yaml(tmp_path, data)
            cfg = CloudEngineerConfig.from_yaml(path)
            assert cfg.backends["test"].env["MY_VAR"] == "interpolated_value"
        finally:
            del os.environ["CLOUD_ENGINEER_TEST_VAR"]

    def test_env_var_missing_keeps_placeholder(self, tmp_path: Path) -> None:
        data = {
            "backends": {
                "test": {
                    "display_name": "Test",
                    "command": "echo",
                    "env": {"MY_VAR": "${NONEXISTENT_VAR_12345}"},
                }
            }
        }
        path = _write_yaml(tmp_path, data)
        cfg = CloudEngineerConfig.from_yaml(path)
        assert cfg.backends["test"].env["MY_VAR"] == "${NONEXISTENT_VAR_12345}"

    def test_invalid_config_raises(self, tmp_path: Path) -> None:
        data = {
            "backends": {
                "test": {
                    "command": "echo",  # missing display_name
                }
            }
        }
        path = _write_yaml(tmp_path, data)
        with pytest.raises((KeyError, TypeError, ValueError)):
            CloudEngineerConfig.from_yaml(path)

    def test_redacted_hides_secrets(self) -> None:
        cfg = CloudEngineerConfig(
            backends={
                "test": {
                    "display_name": "Test",
                    "command": "echo",
                    "env": {
                        "AWS_SECRET_ACCESS_KEY": "super-secret",
                        "AWS_REGION": "us-east-1",
                    },
                }
            }
        )
        redacted = cfg.redacted()
        env = redacted["backends"]["test"]["env"]
        assert env["AWS_SECRET_ACCESS_KEY"] == "***REDACTED***"
        assert env["AWS_REGION"] == "us-east-1"

    def test_transport_config(self, tmp_path: Path) -> None:
        data = {
            "server": {
                "transports": {
                    "stdio": {"enabled": False},
                    "http": {"enabled": True, "port": 9090, "host": "127.0.0.1"},
                }
            }
        }
        path = _write_yaml(tmp_path, data)
        cfg = CloudEngineerConfig.from_yaml(path)
        assert cfg.server.transports.stdio.enabled is False
        assert cfg.server.transports.http.port == 9090
        assert cfg.server.transports.http.host == "127.0.0.1"


class TestBackendUrlValidation:
    def test_empty_url_allowed_for_stdio(self) -> None:
        b = BackendConfig(display_name="Stdio", command="echo")
        assert b.url == ""

    @pytest.mark.parametrize(
        "url",
        ["https://mcp.example.com/mcp", "http://127.0.0.1:9000/mcp"],
    )
    def test_http_and_https_allowed(self, url: str) -> None:
        b = BackendConfig(display_name="Remote", transport="http", url=url)
        assert b.url == url

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://internal/secrets",
            "gopher://10.0.0.1",
            "/etc/passwd",
            "https://",
        ],
    )
    def test_dangerous_schemes_rejected(self, url: str) -> None:
        with pytest.raises(ValidationError):
            BackendConfig(display_name="Bad", transport="http", url=url)

    def test_rejected_via_yaml_load(self, tmp_path: Path) -> None:
        data = {
            "backends": {
                "evil": {
                    "display_name": "Evil",
                    "transport": "http",
                    "url": "file:///etc/passwd",
                }
            }
        }
        path = _write_yaml(tmp_path, data)
        with pytest.raises(ValidationError):
            CloudEngineerConfig.from_yaml(path)
