"""Optional operational notifications (portal §11).

If ``notify_webhook_url`` (portal setting) is set, events POST there in the
Teams incoming-webhook shape ({"text": ...}) — works for Teams, Slack (with a
shim), or any webhook receiver. Email delivery reuses the Graph alerts channel
when configured. Failures never break sync work.
"""

from __future__ import annotations

import httpx

from ..core.logging import get_logger

log = get_logger(__name__)


async def notify(session, event: str, message: str) -> None:
    from .profiles import get_portal_settings  # local import avoids cycles

    settings = get_portal_settings(session)
    url = (settings.get("notify_webhook_url") or "").strip()
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json={"text": f"**[Interlinked Sync] {event}**\n{message}"})
    except Exception as exc:
        log.warning("Notification failed (%s): %s", event, exc)
