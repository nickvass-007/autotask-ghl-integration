"""GHL duplicate-contact resolution on create (location dedupe policy)."""

from __future__ import annotations

import httpx
import pytest
import respx

from integration.canonical.entities import CanonicalContact
from integration.connectors.ghl import GHL_API_BASE, GHLConnector, OAuthToken
from integration.db.enums import Environment, System

pytestmark = pytest.mark.asyncio

DUP = {
    "statusCode": 400,
    "message": "This location does not allow duplicated contacts.",
    "meta": {"contactName": "Existing Person", "contactId": "ghl-existing-1"},
}


def _connector() -> GHLConnector:
    return GHLConnector(
        Environment.SANDBOX,
        client=httpx.AsyncClient(),
        token=OAuthToken(access_token="t", refresh_token="r"),
    )


def _contact() -> CanonicalContact:
    return CanonicalContact(
        source_system=System.AUTOTASK,
        source_id="500",
        email="cathy@sgch.com.au",
        first_name="Cathy",
        last_name="Wilte",
        phone="0295551234",
    )


@respx.mock
async def test_phone_duplicate_retries_without_phone():
    route = respx.post(f"{GHL_API_BASE}/contacts/").mock(
        side_effect=[
            httpx.Response(400, json={**DUP, "meta": {**DUP["meta"], "matchingField": "phone"}}),
            httpx.Response(201, json={"contact": {"id": "ghl-new-2"}}),
        ]
    )
    result = await _connector().create_contact(_contact())
    assert result.ok and result.external_id == "ghl-new-2"
    assert route.call_count == 2
    second_body = route.calls[1].request.content
    assert b"phone" not in second_body  # shared office line dropped, person kept


@respx.mock
async def test_email_duplicate_adopts_existing_contact():
    respx.post(f"{GHL_API_BASE}/contacts/").mock(
        return_value=httpx.Response(
            400, json={**DUP, "meta": {**DUP["meta"], "matchingField": "email"}}
        )
    )
    result = await _connector().create_contact(_contact())
    assert result.ok
    assert result.external_id == "ghl-existing-1"
    assert result.detail == "deduped_existing:email"


@respx.mock
async def test_unrelated_400_still_raises():
    respx.post(f"{GHL_API_BASE}/contacts/").mock(
        return_value=httpx.Response(400, json={"statusCode": 400, "message": "bad payload"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await _connector().create_contact(_contact())
