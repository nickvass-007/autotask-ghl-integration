"""In-process fake connectors for offline tests.

They implement just the methods the Contacts flow calls, returning canned data and
recording writes, so we can assert on what WOULD be written to Autotask without a
live API. This stands in for recorded sandbox responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from integration.canonical.entities import CanonicalCompany, CanonicalContact
from integration.connectors.base import PushResult
from integration.db.enums import System


@dataclass
class FakeAutotask:
    contacts: dict[str, CanonicalContact] = field(default_factory=dict)
    accounts: list[CanonicalCompany] = field(default_factory=list)
    created_contacts: list[CanonicalContact] = field(default_factory=list)
    created_accounts: list[CanonicalCompany] = field(default_factory=list)
    updates: list[tuple[str, dict]] = field(default_factory=list)
    _next_id: int = 1000

    async def find_contacts(self, *, email: str | None = None) -> list[CanonicalContact]:
        if not email:
            return []
        return [c for c in self.contacts.values() if (c.email or "").lower() == email.lower()]

    async def get_contact(self, external_id: str) -> CanonicalContact | None:
        return self.contacts.get(str(external_id))

    async def find_accounts(self, name: str) -> list[CanonicalCompany]:
        if not name:
            return []
        return [a for a in self.accounts if (a.name or "").lower().startswith(name.lower()[:3])]

    async def create_contact(self, contact: CanonicalContact) -> PushResult:
        if contact.company_id is None:
            return PushResult(ok=False, detail="no companyID")
        self._next_id += 1
        new_id = str(self._next_id)
        stored = CanonicalContact(
            source_system=System.AUTOTASK,
            source_id=new_id,
            email=contact.email,
            first_name=contact.first_name,
            last_name=contact.last_name,
            phone=contact.phone,
            company_id=contact.company_id,
        )
        self.contacts[new_id] = stored
        self.created_contacts.append(stored)
        return PushResult(ok=True, external_id=new_id, detail="created")

    async def update_contact(self, external_id: str, changes: dict) -> PushResult:
        self.updates.append((str(external_id), dict(changes)))
        existing = self.contacts.get(str(external_id))
        if existing:
            for k, v in changes.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
        return PushResult(ok=True, external_id=str(external_id), detail="updated")

    async def create_account(self, company: CanonicalCompany) -> PushResult:
        self._next_id += 1
        new_id = str(self._next_id)
        company.company_id = new_id
        self.created_accounts.append(company)
        self.accounts.append(company)
        return PushResult(ok=True, external_id=new_id, detail="account_created")


@dataclass
class FakeGHL:
    contacts: dict[str, CanonicalContact] = field(default_factory=dict)
    created: list[CanonicalContact] = field(default_factory=list)
    updates: list[tuple[str, dict]] = field(default_factory=list)
    _next_id: int = 1

    async def get_contact(self, external_id: str) -> CanonicalContact | None:
        return self.contacts.get(str(external_id))

    async def create_contact(self, contact: CanonicalContact) -> PushResult:
        self._next_id += 1
        new_id = f"ghl{self._next_id}"
        self.created.append(contact)
        return PushResult(ok=True, external_id=new_id, detail="created")

    async def update_contact(self, external_id: str, changes: dict) -> PushResult:
        self.updates.append((str(external_id), dict(changes)))
        return PushResult(ok=True, external_id=str(external_id), detail="updated")
