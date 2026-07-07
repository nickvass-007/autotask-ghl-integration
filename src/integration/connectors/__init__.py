"""Connectors — one spoke per external system, mapping to the canonical hub.

Autotask and GHL are the first two implementations of ``Connector`` (Spec §7.2).
A third connector (e.g. 3CX) is added by implementing the same contract — see
CONTRIBUTING.md.
"""

from .base import (
    ChangeSet,
    Connector,
    ConnectorCapabilities,
    PushResult,
    RateLimit,
)

__all__ = [
    "Connector",
    "ConnectorCapabilities",
    "RateLimit",
    "ChangeSet",
    "PushResult",
]
