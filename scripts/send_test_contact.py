"""Send a signed test GHL contact webhook to the running app.

Computes the HMAC-SHA256 signature the connector expects (using GHL_WEBHOOK_SECRET
from your .env) and POSTs a sample contact to /webhooks/ghl/contact, so you can
watch the gated Contacts flow run against your Autotask sandbox without waiting for
a real GHL webhook.

Usage (with the app running and .env filled):
    py -3.12 scripts/send_test_contact.py
    py -3.12 scripts/send_test_contact.py --email jane@newco.com --company "New Co"
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000/webhooks/ghl/contact")
    parser.add_argument("--email", default="jane.tester@example-newco.com")
    parser.add_argument("--first", default="Jane")
    parser.add_argument("--last", default="Tester")
    parser.add_argument("--company", default="Example New Co")
    parser.add_argument("--id", default="ghl-test-0001")
    parser.add_argument("--event", default=None, help="event id (defaults to a unique value)")
    args = parser.parse_args()

    env = load_env(Path(__file__).resolve().parents[1] / ".env")
    secret = env.get("GHL_WEBHOOK_SECRET") or os.environ.get("GHL_WEBHOOK_SECRET", "")
    if not secret:
        raise SystemExit("GHL_WEBHOOK_SECRET not found in .env — fill it in first.")

    # A fresh event id each run unless you pass --event (so idempotency lets it through).
    event_id = args.event or f"evt-{args.id}-{os.urandom(4).hex()}"
    payload = {
        "eventId": event_id,
        "contact": {
            "id": args.id,
            "email": args.email,
            "firstName": args.first,
            "lastName": args.last,
            "companyName": args.company,
        },
    }
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    req = Request(
        args.url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "x-wh-signature": signature},
    )
    with urlopen(req) as resp:  # noqa: S310 (local dev tool)
        print(f"HTTP {resp.status}")
        print(resp.read().decode("utf-8"))


if __name__ == "__main__":
    main()
