"""Environment switch defaults to sandbox and is visible (Spec §6)."""

from __future__ import annotations

from integration.config.settings import Settings
from integration.db.enums import Environment


def test_defaults_to_sandbox():
    s = Settings(_env_file=None)
    assert s.environment is Environment.SANDBOX
    assert s.is_production is False


def test_banner_states_active_environment():
    s = Settings(_env_file=None)
    assert "SANDBOX" in s.environment_banner()


def test_production_is_explicit_and_marked():
    s = Settings(_env_file=None, environment="production")
    assert s.is_production is True
    assert "PRODUCTION" in s.environment_banner()
