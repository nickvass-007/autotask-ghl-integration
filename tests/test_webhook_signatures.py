"""GHL webhook signature verification (Spec §4): Ed25519, legacy HMAC, fail-closed.

GHL signs marketplace webhooks with ITS private key; we can't produce a valid
signature with the real key, so the Ed25519 path is tested by swapping in a
locally generated keypair via monkeypatch — the verification code path is
identical either way.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from integration.connectors import ghl as ghl_module
from integration.connectors.ghl import GHLConnector
from integration.db.enums import Environment

BODY = b'{"eventId": "evt-1", "contact": {"id": "c-1", "email": "a@b.co"}}'


def _connector() -> GHLConnector:
    return GHLConnector(Environment.SANDBOX)


def test_ed25519_signature_accepted(monkeypatch):
    private = Ed25519PrivateKey.generate()
    pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setattr(ghl_module, "GHL_ED25519_PUBLIC_KEY_PEM", pem)
    sig = base64.b64encode(private.sign(BODY)).decode()
    assert _connector().verify_webhook({"x-ghl-signature": sig}, BODY) is True


def test_ed25519_tampered_body_rejected(monkeypatch):
    private = Ed25519PrivateKey.generate()
    pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setattr(ghl_module, "GHL_ED25519_PUBLIC_KEY_PEM", pem)
    sig = base64.b64encode(private.sign(BODY)).decode()
    assert _connector().verify_webhook({"x-ghl-signature": sig}, BODY + b"x") is False


def test_ed25519_garbage_signature_rejected():
    # Against the real GHL key: not base64 / not a valid signature — fail closed.
    assert _connector().verify_webhook({"x-ghl-signature": "not-base64!!"}, BODY) is False
    sig = base64.b64encode(b"\x00" * 64).decode()
    assert _connector().verify_webhook({"x-ghl-signature": sig}, BODY) is False


def test_hmac_local_testing_path(monkeypatch):
    secret = "local-test-secret"
    connector = _connector()
    monkeypatch.setattr(connector._settings, "ghl_webhook_secret", secret)
    sig = hmac.new(secret.encode(), BODY, hashlib.sha256).hexdigest()
    assert connector.verify_webhook({"x-wh-signature": sig}, BODY) is True
    assert connector.verify_webhook({"x-wh-signature": sig}, BODY + b"x") is False


def test_no_signature_rejected():
    assert _connector().verify_webhook({}, BODY) is False


def test_header_lookup_is_case_insensitive(monkeypatch):
    secret = "local-test-secret"
    connector = _connector()
    monkeypatch.setattr(connector._settings, "ghl_webhook_secret", secret)
    sig = hmac.new(secret.encode(), BODY, hashlib.sha256).hexdigest()
    assert connector.verify_webhook({"X-Wh-Signature": sig}, BODY) is True
