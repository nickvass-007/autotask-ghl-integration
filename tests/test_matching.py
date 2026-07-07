"""Dedupe precedence (Spec §9.1)."""

from __future__ import annotations

from integration.canonical.entities import CanonicalContact
from integration.db.enums import System
from integration.sync.matching import MatchClass, match_contact


def ghl(**kw) -> CanonicalContact:
    return CanonicalContact(source_system=System.GHL, **kw)


def at(**kw) -> CanonicalContact:
    return CanonicalContact(source_system=System.AUTOTASK, **kw)


def test_email_exact_is_confident():
    incoming = ghl(email="jane@acme.com", first_name="Jane")
    candidates = [at(source_id="1", email="jane@acme.com", first_name="J")]
    result = match_contact(incoming, candidates)
    assert result.classification is MatchClass.CONFIDENT
    assert result.rule.startswith("1:email")
    assert result.matched.source_id == "1"


def test_name_plus_company_exact_is_confident():
    incoming = ghl(first_name="Jane", last_name="Doe", company_id="55")
    candidates = [at(source_id="2", first_name="Jane", last_name="Doe", company_id="55")]
    assert match_contact(incoming, candidates).classification is MatchClass.CONFIDENT


def test_phone_plus_name_similarity_is_possible():
    incoming = ghl(first_name="Jonathan", last_name="Smith", phone="0400111222")
    candidates = [at(source_id="3", first_name="Jon", last_name="Smith", phone="0400111222")]
    assert match_contact(incoming, candidates).classification is MatchClass.POSSIBLE


def test_no_candidates_is_clean_miss():
    assert match_contact(ghl(email="new@x.com"), []).classification is MatchClass.CLEAN_MISS


def test_unrelated_candidate_is_clean_miss():
    incoming = ghl(email="new@x.com", first_name="Alice", company_name="Zeta")
    candidates = [at(source_id="9", email="bob@y.com", first_name="Bob", company_name="Acme")]
    assert match_contact(incoming, candidates).classification is MatchClass.CLEAN_MISS
