"""Canonical internal model — the hub of the hub-and-spoke design (Spec §7.1).

Every connector maps **its** schema to these system-agnostic entities only. Adding
system N is one set of mappings to the hub, not N mappings to every other system.
"""

from .entities import CanonicalCompany, CanonicalContact, CanonicalDeal, CanonicalServiceItem

__all__ = [
    "CanonicalContact",
    "CanonicalCompany",
    "CanonicalDeal",
    "CanonicalServiceItem",
]
