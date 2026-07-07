"""Customer sync criteria — which Autotask customers flow to GHL.

Operator-defined rules (managed in the /admin UI, stored in ``sync_criteria``)
gate the OUTBOUND direction only: the Autotask→GHL contact mirror and the
Stage-E classification push. Rules AND together and are evaluated against the
contact's **Account** raw fields (e.g. ``companyType``, ``classification``,
``isActive``). No active rules = everything syncs (the pre-feature behaviour).

⚠️ Deliberately NOT applied to the inbound gated direction (GHL→Autotask): the
protection rules there are about safety, not audience selection — a webhook for
an out-of-audience contact still goes through dedupe/approvals as normal.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config.settings import get_settings
from ..core.logging import get_logger
from ..db.models import SyncCriteria

log = get_logger(__name__)

OPERATORS = ("eq", "ne", "in", "not_in")

# Account fields offered in the admin UI. Picklist-backed ones get their live
# labels fetched from Autotask so operators pick by name, not by numeric id.
FILTERABLE_FIELDS = {
    "companyType": {"label": "Customer type", "picklist": True},
    "classification": {"label": "Classification", "picklist": True},
    "isActive": {"label": "Account active", "picklist": False},
    "ownerResourceID": {"label": "Account owner (resource id)", "picklist": False},
    "marketSegmentID": {"label": "Market segment", "picklist": True},
}


def load_rules(session: Session, entity_type: str = "contact") -> list[SyncCriteria]:
    env = get_settings().environment
    stmt = select(SyncCriteria).where(
        SyncCriteria.environment == env,
        SyncCriteria.entity_type == entity_type,
        SyncCriteria.active.is_(True),
    )
    return list(session.execute(stmt).scalars().all())


def matches(account_raw: dict | None, rules: list[SyncCriteria]) -> bool:
    """AND all active rules against the raw Account fields. Fail CLOSED on a
    missing account when rules exist: an unfilterable record does not sync."""
    if not rules:
        return True
    if account_raw is None:
        return False
    for rule in rules:
        raw = account_raw.get(rule.field)
        actual = "" if raw is None else str(raw).strip().lower()
        wanted = [v.strip().lower() for v in rule.value.split(",") if v.strip()]
        if rule.operator == "eq":
            ok = actual in wanted if len(wanted) > 1 else actual == (wanted[0] if wanted else "")
        elif rule.operator == "ne":
            ok = actual not in wanted
        elif rule.operator == "in":
            ok = actual in wanted
        elif rule.operator == "not_in":
            ok = actual not in wanted
        else:
            log.warning("Unknown criteria operator %r — rule #%s ignored", rule.operator, rule.id)
            ok = True
        if not ok:
            return False
    return True


class AccountFilter:
    """Per-sweep evaluator with an account cache so each Account is fetched once."""

    def __init__(self, session: Session, autotask) -> None:
        self._rules = load_rules(session)
        self._autotask = autotask
        self._cache: dict[str, bool] = {}

    @property
    def has_rules(self) -> bool:
        return bool(self._rules)

    async def allows_account(self, account_id: str | None) -> bool:
        if not self._rules:
            return True
        if account_id is None:
            return False
        if account_id not in self._cache:
            raw = await self._autotask.get_account_raw(account_id)
            self._cache[account_id] = matches(raw, self._rules)
        return self._cache[account_id]

    async def allows_contact(self, at_contact) -> bool:
        if not self._rules:
            return True
        if at_contact is None:
            return False
        return await self.allows_account(at_contact.company_id)
