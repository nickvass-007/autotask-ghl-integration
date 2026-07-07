"""Pluggable matching / dedupe engine (Spec §9.1, §7.5).

Implements the Contact dedupe precedence. It is deliberately generic and reusable:
the same classify-and-route mechanism is what a future connector (e.g. 3CX
matching a recording to a Contact by phone) would reuse (Spec §7.5).

Precedence (Spec §9.1):
  1. email exact                              -> CONFIDENT (primary key)
  2. firstName + lastName + companyID exact   -> CONFIDENT
  3. phone/mobile exact + name similarity     -> POSSIBLE (approval)
  4. fuzzy name + company                     -> POSSIBLE (approval)
  5. nothing                                  -> CLEAN_MISS (safe to create)
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from ..canonical.entities import CanonicalContact

# Tunable thresholds (kept here, not scattered through the engine).
NAME_SIMILARITY_THRESHOLD = 0.82
FUZZY_NAME_THRESHOLD = 0.78
FUZZY_COMPANY_THRESHOLD = 0.80


class MatchClass(StrEnum):
    CONFIDENT = "confident"   # safe to link automatically
    POSSIBLE = "possible"     # ambiguous -> approval (never auto-create/link)
    CLEAN_MISS = "clean_miss"  # nothing matched -> safe to create


@dataclass(slots=True)
class MatchResult:
    classification: MatchClass
    rule: str
    matched: CanonicalContact | None = None
    candidates: list[CanonicalContact] | None = None

    @property
    def is_confident(self) -> bool:
        return self.classification is MatchClass.CONFIDENT

    @property
    def is_clean_miss(self) -> bool:
        return self.classification is MatchClass.CLEAN_MISS


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _name_similarity(a: CanonicalContact, b: CanonicalContact) -> float:
    return SequenceMatcher(None, _norm(a.full_name()), _norm(b.full_name())).ratio()


def match_contact(
    incoming: CanonicalContact,
    candidates: list[CanonicalContact],
) -> MatchResult:
    """Classify an incoming (GHL) contact against Autotask candidates (Spec §9.1).

    ``candidates`` is the pooled set the orchestrator gathered (by email, name,
    and/or phone). This function is pure — no I/O — so it is trivially testable."""
    if not candidates:
        return MatchResult(MatchClass.CLEAN_MISS, rule="5:nothing")

    # Rule 1 — email exact (primary key).
    if _norm(incoming.email):
        for c in candidates:
            if _norm(c.email) and _norm(c.email) == _norm(incoming.email):
                return MatchResult(MatchClass.CONFIDENT, rule="1:email", matched=c)

    # Rule 2 — firstName + lastName + companyID exact.
    if _norm(incoming.first_name) and _norm(incoming.last_name) and incoming.company_id:
        for c in candidates:
            if (
                _norm(c.first_name) == _norm(incoming.first_name)
                and _norm(c.last_name) == _norm(incoming.last_name)
                and c.company_id == incoming.company_id
            ):
                return MatchResult(MatchClass.CONFIDENT, rule="2:name+company", matched=c)

    # Rule 3 — phone/mobile exact + name similarity -> possible.
    inc_phones = {_norm(incoming.phone), _norm(incoming.mobile)} - {""}
    if inc_phones:
        for c in candidates:
            cand_phones = {_norm(c.phone), _norm(c.mobile)} - {""}
            if inc_phones & cand_phones and _name_similarity(incoming, c) >= NAME_SIMILARITY_THRESHOLD:
                return MatchResult(
                    MatchClass.POSSIBLE,
                    rule="3:phone+name",
                    matched=c,
                    candidates=[c],
                )

    # Rule 4 — fuzzy name + company -> possible.
    fuzzy: list[CanonicalContact] = []
    for c in candidates:
        name_sim = _name_similarity(incoming, c)
        company_sim = SequenceMatcher(
            None, _norm(incoming.company_name), _norm(c.company_name)
        ).ratio()
        if name_sim >= FUZZY_NAME_THRESHOLD and company_sim >= FUZZY_COMPANY_THRESHOLD:
            fuzzy.append(c)
    if fuzzy:
        return MatchResult(
            MatchClass.POSSIBLE,
            rule="4:fuzzy_name+company",
            matched=fuzzy[0] if len(fuzzy) == 1 else None,
            candidates=fuzzy,
        )

    # Rule 5 — nothing.
    return MatchResult(MatchClass.CLEAN_MISS, rule="5:nothing")
