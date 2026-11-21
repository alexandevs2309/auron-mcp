"""MC-08: el secreto OAuth nunca vive en el repositorio."""

from pathlib import Path

import pytest

from auron_mcp.config import Settings
from auron_mcp.secrets import (
    SHIPPED_OAUTH_CLIENT_SECRET,
    ConfigurationError,
    check_config,
    validate_config,
)

ROOT = Path(__file__).resolve().parent.parent


def test_default_oauth_secret_is_rejected():
    settings = Settings.from_env(
        {"AURON_ADAPTER": "mock", "AURON_MCP_OAUTH_ENABLED": "true"}
    )
    problems = validate_config(settings)
    assert any("AURON_MCP_OAUTH_CLIENT_SECRET" in p for p in problems)
    with pytest.raises(ConfigurationError):
        check_config(settings)


def test_injected_oauth_secret_is_accepted():
    settings = Settings.from_env(
        {
            "AURON_ADAPTER": "mock",
            "AURON_MCP_OAUTH_ENABLED": "true",
            "AURON_MCP_OAUTH_CLIENT_SECRET": "valor-inyectado-por-el-dashboard",
        }
    )
    assert validate_config(settings) == []
    check_config(settings)


def test_render_yaml_injects_secret_without_a_literal_value():
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "AURON_MCP_OAUTH_CLIENT_SECRET" in text
    assert "{env:AURON_MCP_OAUTH_CLIENT_SECRET}" in text
    assert SHIPPED_OAUTH_CLIENT_SECRET not in text
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if "AURON_MCP_OAUTH_CLIENT_SECRET" in line)
    assert any("sync: false" in line for line in lines[index : index + 4])
