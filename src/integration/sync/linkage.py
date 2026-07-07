"""Account (Company) linkage guard — stricter than Contact creation (Spec §9.3).

⚠️ A GHL company-name typo must never rename an Account (attached to many contacts,
tickets, contracts) or spawn a duplicate. Resolution outcomes:

- Exact Account name match              -> link contact.companyID, done.
- One close fuzzy candidate             -> approval: "Link to existing Account 'Y'?"
- Multiple candidates                   -> approval with a pick-list.
- No candidate                          -> approval: "Create new Account 'X'?"
                                           (Accounts are NEVER auto-created.)
- ✅ Unmatched + holding account configured -> attach to the holding Account so the
  contact isn't orphaned while the decision is pending.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from ..canonical.entities import CanonicalCompany

ACCOUNT_FUZZY_THRESHOLD = 0.85


class LinkOutcome(StrEnum):
    LINKED = "linked"               # exact match -> companyID resolved
    HOLDING = "holding"             # attached to holding account pending decision
    APPROVAL_LINK = "approval_link"  # one/many fuzzy candidates -> approval
    APPROVAL_CREATE = "approval_create"  # no candidate -> approval to create Account


@dataclass(slots=True)
class LinkageResult:
    outcome: LinkOutcome
    company_id: str | None = None
    candidates: list[CanonicalCompany] | None = None
    reason: str = ""


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def resolve_account(
    company_name: str | None,
    candidates: list[CanonicalCompany],
    *,
    holding_account_id: str | None = None,
) -> LinkageResult:
    """Decide how a contact's company links to an Autotask Account (Spec §9.3).

    Pure function: the orchestrator supplies the candidate Accounts (fetched from
    Autotask) and the configured holding account; this classifies the outcome."""
    name = _norm(company_name)

    # Exact name match -> safe to link directly.
    exact = [c for c in candidates if _norm(c.name) == name and name]
    if exact:
        return LinkageResult(
            LinkOutcome.LINKED,
            company_id=exact[0].company_id,
            reason=f"Exact Account name match: {exact[0].name!r}",
        )

    # Close fuzzy candidate(s) -> approval (link), never auto-link.
    fuzzy = [
        c
        for c in candidates
        if name and SequenceMatcher(None, name, _norm(c.name)).ratio() >= ACCOUNT_FUZZY_THRESHOLD
    ]
    if fuzzy:
        return LinkageResult(
            LinkOutcome.APPROVAL_LINK,
            candidates=fuzzy,
            reason=(
                f"{len(fuzzy)} possible Account match(es) for {company_name!r} — "
                "human must confirm the link (never auto-link)."
            ),
        )

    # No candidate. Park on the holding account if configured, else approval-to-create.
    if holding_account_id:
        return LinkageResult(
            LinkOutcome.HOLDING,
            company_id=holding_account_id,
            reason=(
                f"No Account match for {company_name!r}; attached to holding account "
                f"{holding_account_id} pending a create/link decision."
            ),
        )
    return LinkageResult(
        LinkOutcome.APPROVAL_CREATE,
        reason=f"No Account match for {company_name!r} — approval required to create a new Account.",
    )
