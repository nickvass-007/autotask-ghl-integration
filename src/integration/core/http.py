"""Shared async HTTP helper with exponential backoff + jitter (Spec §12.1).

Autotask (per-DB threshold) and GHL (burst + daily) both rate-limit. We retry
transient failures (429, 5xx, network) with exponential backoff + jitter via
tenacity, and surface a clear error otherwise. Connectors share one client per
instance so connection pooling and auth headers are reused.
"""

from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .logging import get_logger

log = get_logger(__name__)

# Status codes worth retrying: throttling + transient server errors.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


def with_backoff(max_attempts: int = 5):
    """Decorator applying exponential backoff + jitter to an async request fn."""
    return retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential_jitter(initial=0.5, max=30),
        stop=stop_after_attempt(max_attempts),
        reraise=True,
    )


@with_backoff()
async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: object,
) -> httpx.Response:
    """Issue a request, raise for status (so retries trigger), return the response."""
    response = await client.request(method, url, **kwargs)  # type: ignore[arg-type]
    if response.status_code >= 400:
        # Surface the API's error body — without this, 4xx/5xx diagnoses are blind.
        log.warning(
            "HTTP %s %s -> %s: %s", method, url, response.status_code, response.text[:1000]
        )
    response.raise_for_status()
    return response
