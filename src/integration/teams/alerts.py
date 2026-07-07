"""Backup alert channel — email to admins via Microsoft Graph (Spec §11).

For high-severity items (HIGH approvals, circuit-breaker trips, token/credential
expiry) we also email admins so a Teams outage never hides a critical event. This
is best-effort: a Graph failure is logged, never allowed to break the sync path.
"""

from __future__ import annotations

import httpx

from ..config.settings import get_settings
from ..core.logging import get_logger

log = get_logger(__name__)

_GRAPH_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_GRAPH_SENDMAIL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"


async def send_admin_alert(subject: str, body: str) -> bool:
    """Email all configured admins. Returns False (logged) on any failure."""
    s = get_settings()
    if not (s.graph_tenant_id and s.graph_client_id and s.admin_email_list):
        log.info("Graph alert skipped (not configured): %s", subject)
        return False
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            token_resp = await client.post(
                _GRAPH_TOKEN_URL.format(tenant=s.graph_tenant_id),
                data={
                    "client_id": s.graph_client_id,
                    "client_secret": s.graph_client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )
            token_resp.raise_for_status()
            access = token_resp.json()["access_token"]
            sender = s.admin_email_list[0]
            message = {
                "message": {
                    "subject": f"[{s.environment.value}] {subject}",
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": e}} for e in s.admin_email_list],
                }
            }
            resp = await client.post(
                _GRAPH_SENDMAIL.format(sender=sender),
                headers={"Authorization": f"Bearer {access}"},
                json=message,
            )
            resp.raise_for_status()
        return True
    except Exception as exc:  # best-effort backup channel
        log.error("Graph admin alert failed: %s", exc)
        return False
