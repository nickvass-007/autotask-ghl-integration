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
async def test_invalid_email_dropped_when_phone_remains():
    route = respx.post(f"{GHL_API_BASE}/contacts/").mock(
        side_effect=[
            httpx.Response(422, json={"message": ["email must be an email"], "statusCode": 422}),
            httpx.Response(201, json={"contact": {"id": "ghl-new-3"}}),
        ]
    )
    result = await _connector().create_contact(_contact())
    assert result.ok and result.external_id == "ghl-new-3"
    assert result.detail == "created_dropped:email(invalid)"
    assert b'"email"' not in route.calls[1].request.content


@respx.mock
async def test_invalid_email_with_no_phone_still_fails():
    respx.post(f"{GHL_API_BASE}/contacts/").mock(
        return_value=httpx.Response(
            422, json={"message": ["email must be an email"], "statusCode": 422}
        )
    )
    contact = _contact()
    contact.phone = None
    with pytest.raises(httpx.HTTPStatusError):
        await _connector().create_contact(contact)


@respx.mock
async def test_invalid_phone_dropped_when_email_remains():
    route = respx.post(f"{GHL_API_BASE}/contacts/").mock(
        side_effect=[
            httpx.Response(
                400,
                json={
                    "message": "The string supplied did not seem to be a phone number",
                    "statusCode": 400,
                },
            ),
            httpx.Response(201, json={"contact": {"id": "ghl-new-4"}}),
        ]
    )
    result = await _connector().create_contact(_contact())
    assert result.ok and result.detail == "created_dropped:phone(invalid)"
    assert b'"phone"' not in route.calls[1].request.content


@respx.mock
async def test_update_salvages_invalid_fields():
    route = respx.put(f"{GHL_API_BASE}/contacts/ghl-1").mock(
        side_effect=[
            httpx.Response(422, json={"message": ["email must be an email"], "statusCode": 422}),
            httpx.Response(200, json={"contact": {"id": "ghl-1"}}),
        ]
    )
    result = await _connector().update_contact(
        "ghl-1", {"email": "not-an-email", "firstName": "Cathy"}
    )
    assert result.ok and result.detail == "updated_dropped:email(invalid)"
    assert b'"email"' not in route.calls[1].request.content


@respx.mock
async def test_update_skips_when_every_field_invalid():
    respx.put(f"{GHL_API_BASE}/contacts/ghl-1").mock(
        return_value=httpx.Response(
            400,
            json={"statusCode": 400, "message": "The string supplied did not seem to be a phone number"},
        )
    )
    result = await _connector().update_contact("ghl-1", {"phone": "TBA"})
    assert result.ok and result.detail == "skipped_dropped:phone(invalid)"


@respx.mock
async def test_unrelated_400_still_raises():
    respx.post(f"{GHL_API_BASE}/contacts/").mock(
        return_value=httpx.Response(400, json={"statusCode": 400, "message": "bad payload"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await _connector().create_contact(_contact())
