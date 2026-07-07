"""Account-linkage guard (Spec §9.3) and field conflict detection (Spec §5.2, §9.2)."""

from __future__ import annotations

from integration.canonical.entities import CanonicalCompany, CanonicalContact
from integration.config.mapping import load_contacts_mapping
from integration.db.enums import System
from integration.sync.conflicts import detect_changes
from integration.sync.linkage import LinkOutcome, resolve_account


def company(name: str, cid: str) -> CanonicalCompany:
    c = CanonicalCompany(source_system=System.AUTOTASK, name=name)
    c.company_id = cid
    return c


def test_exact_account_name_links():
    res = resolve_account("Acme Pty Ltd", [company("Acme Pty Ltd", "10")])
    assert res.outcome is LinkOutcome.LINKED
    assert res.company_id == "10"


def test_fuzzy_account_requires_approval_not_autolink():
    res = resolve_account("Acme Pty Ltdd", [company("Acme Pty Ltd", "10")])
    assert res.outcome is LinkOutcome.APPROVAL_LINK  # never auto-link a near match


def test_no_account_no_holding_requires_create_approval():
    res = resolve_account("Brand New Co", [])
    assert res.outcome is LinkOutcome.APPROVAL_CREATE  # Accounts never auto-created


def test_no_account_with_holding_parks_on_holding():
    res = resolve_account("Brand New Co", [], holding_account_id="HOLD1")
    assert res.outcome is LinkOutcome.HOLDING
    assert res.company_id == "HOLD1"


def test_empty_field_is_additive_populated_is_conflict():
    mapping = load_contacts_mapping()
    incoming = CanonicalContact(
        source_system=System.GHL, first_name="Jane", phone="0400999888"
    )
    existing = CanonicalContact(
        source_system=System.AUTOTASK, first_name="Janet", phone=None
    )
    changes = detect_changes(incoming, existing, mapping)
    # phone empty in AT -> additive; first_name populated & differs -> conflict
    assert "phone" in changes.additive
    assert any(c.canonical == "first_name" for c in changes.conflicts)


def test_company_name_excluded_from_field_conflicts():
    mapping = load_contacts_mapping()
    incoming = CanonicalContact(source_system=System.GHL, company_name="Typo Co")
    existing = CanonicalContact(source_system=System.AUTOTASK, company_name="Real Co")
    changes = detect_changes(incoming, existing, mapping)
    # company renames go through the linkage guard, never a plain field write
    assert not any(c.canonical == "company_name" for c in changes.conflicts)
