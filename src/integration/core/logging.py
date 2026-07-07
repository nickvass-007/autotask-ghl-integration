"""Structured logging with correlation IDs (Spec §11, §12.2).

Every sync operation gets a ``correlation_id`` that threads through the
transaction feed, audit log, and approval queue so "what happened to this
contact?" can be answered end to end. ``new_correlation_id`` is deterministic-free
(uuid4) and set per inbound event.
"""

from __future__ import annotations

import logging
import sys
import uuid

_CONFIGURED = False


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def configure_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
