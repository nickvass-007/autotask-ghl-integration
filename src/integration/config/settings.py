"""Runtime settings, loaded from environment / .env (Spec §6, §12).

The environment (`sandbox` | `production`) is a **runtime config switch, never
hardcoded**, and **defaults to sandbox**. Sandbox and production each carry their
own credential set; one environment can never read the other's (Spec §6).

⚠️ Operating against production must be a deliberate, obvious act. ``is_production``
is surfaced in the startup banner, the /health endpoint, and the Teams status
command so the active environment is always visible.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..db.enums import Environment


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Environment switch (defaults to sandbox) ──
    environment: Environment = Environment.SANDBOX
    deployment_label: str = "Local development"

    # ── Database ──
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/autotask_ghl"

    # ── Autotask (per-environment credentials) ──
    autotask_username: str = ""
    autotask_secret: str = ""
    autotask_integration_code: str = ""
    autotask_zone_override_url: str = ""
    # Holding Account for contacts awaiting a linkage decision so nothing floats
    # orphaned (Spec §9.3). Blank = require explicit approval instead of holding.
    autotask_holding_account_id: str = ""

    # ── GoHighLevel ──
    ghl_client_id: str = ""
    ghl_client_secret: str = ""
    # ⚠️ Path must not contain "ghl"/"highlevel" — GHL's marketplace validator
    # rejects redirect URIs that reference their brand.
    ghl_redirect_uri: str = "http://localhost:8000/oauth/crm/callback"
    ghl_location_id: str = ""
    ghl_webhook_secret: str = ""
    ghl_scopes: str = "contacts.readonly contacts.write"

    # ── Teams bot ──
    teams_bot_app_id: str = ""
    teams_bot_app_password: str = ""
    teams_bot_tenant_id: str = ""
    approval_callback_secret: str = ""

    # ── Microsoft Graph (backup email) ──
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    admin_alert_emails: str = ""

    # ── Circuit breaker (Spec §5.5) ──
    circuit_breaker_max_conflicts: int = 25
    circuit_breaker_max_failure_rate: float = 0.30
    circuit_breaker_window_minutes: int = 15

    # ── Approvals ──
    approval_expiry_hours: int = 48
    auto_apply_additive: bool = True

    # ── Polling / reconciliation ──
    autotask_poll_interval_seconds: int = 300
    reconciliation_interval_seconds: int = 3600
    # ⚠️ Off by default: the poller mirrors Autotask changes into GHL continuously.
    # Locally it runs as a background task (scripts/run_poller.py or ENABLE_POLLER);
    # in production it moves to Timer-triggered Functions (Spec §12.2).
    enable_poller: bool = False
    # Portal scheduler for saved sync profiles. Safe-on: profiles only run if an
    # operator scheduled them, and live runs require dry-run + approval gates.
    enable_scheduler: bool = True

    # ── Stage C — conversion handoff (Spec §8.2) ──
    # The stamp applied to a GHL contact once its record lives in Autotask.
    converted_tag: str = "Converted — Managed in Autotask"

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def ghl_scope_list(self) -> list[str]:
        return [s for s in self.ghl_scopes.replace(",", " ").split() if s]

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip() for e in self.admin_alert_emails.split(",") if e.strip()]

    def environment_banner(self) -> str:
        """Single-line, unmissable statement of the active environment."""
        marker = "🔴 PRODUCTION" if self.is_production else "🟢 SANDBOX"
        return f"ACTIVE ENVIRONMENT: {marker}  ({self.deployment_label})"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton. Use this everywhere rather than constructing Settings()."""
    return Settings()
