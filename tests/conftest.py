"""Shared test fixtures for cloud-engineer-mcp tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.types import Tool

from cloud_engineer_mcp.config import CloudEngineerConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_tools() -> list[Tool]:
    """Load sample tools from the fixture file."""
    with open(FIXTURES_DIR / "sample_tools.json") as f:
        data = json.load(f)
    return [Tool(**t) for t in data]


@pytest.fixture
def test_config_path(tmp_path: Path) -> Path:
    """Create a minimal test config file."""
    config = {
        "server": {"name": "cloud-engineer-mcp-test"},
        "selector": {"model_name": "all-MiniLM-L6-v2", "top_k": 5, "min_similarity": 0.1},
        "backends": {
            "mock_a": {
                "display_name": "Mock Backend A",
                "command": "python",
                "args": ["-m", "tests.fixtures.mock_backend"],
                "enabled": True,
            },
            "mock_b": {
                "display_name": "Mock Backend B",
                "command": "python",
                "args": ["-m", "tests.fixtures.mock_backend"],
                "enabled": True,
            },
        },
    }
    import yaml

    cfg_path = tmp_path / "test_config.yml"
    with open(cfg_path, "w") as f:
        yaml.dump(config, f)
    return cfg_path


@pytest.fixture
def test_config(test_config_path: Path) -> CloudEngineerConfig:
    """Load the test config."""
    return CloudEngineerConfig.from_yaml(test_config_path)
