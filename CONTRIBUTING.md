# Contributing & extending — how to add a third connector

This integration is **GHL ↔ Autotask first**, but it's built as a **platform**: new
systems (e.g. **3CX** call-transcription ingest — *illustrative only, not built*)
plug in **without rewriting the core** (Spec §7). This guide shows how.

## The shape of the system

```
   External system  ──maps to──►  Canonical hub  ◄──maps from──  External system
   (connector spoke)              (Contact/Company/Deal/…)        (connector spoke)
```

- **Canonical model** (`src/integration/canonical/`) is the hub. Every connector
  maps **its** schema to these system-agnostic entities only. Adding system *N* is
  one set of mappings to the hub — **not** *N* mappings to every other system.
- **Connector contract** (`src/integration/connectors/base.py`) is the common
  interface: `authenticate()`, `fetch_changes()`, `push()` (via the typed
  create/update methods), `handle_webhook()` / `verify_webhook()`, plus declared
  **capabilities** (read/write, supports-webhooks, rate limits).
- **Matching + approval pipeline** (`src/integration/sync/`) is generic and reused
  by every connector — dedupe → classify → gate → approve → audit.
- **Per-connector config** is YAML in `config/` with source-of-truth rules against
  the canonical model. Precedence is configurable per entity/field
  (Autotask-protective today; a different system could own a different entity).

## Add a connector in 6 steps

Worked example: a hypothetical **3CX** connector that ingests call recordings and
attaches them to the matching Contact (Spec §7.5).

1. **Model the entity in the hub (if new).** Calls aren't a Contact, so add a
   `CanonicalCommunication` to `canonical/entities.py` extending `CanonicalEntity`
   (the base already anticipates this — Spec §8.4). Reuse existing entities where
   you can.

2. **Implement the contract.** Create `connectors/threecx.py` with a class
   subclassing `Connector`:
   - Declare `capabilities` (`ConnectorCapabilities`): key, can_read/write,
     supports_webhooks, supported_entities, `RateLimit`.
   - Implement `authenticate()`, `health()`, `fetch_changes()`,
     `verify_webhook()`, and the read/write methods your entity needs.
   - Translate the vendor payload ↔ canonical entity inside the connector. The core
     never sees vendor JSON.

3. **Add mapping + source-of-truth YAML.** Create `config/threecx.mapping.yaml` in
   the same shape as `config/contacts.mapping.yaml`: which canonical field maps to
   which vendor field, and the per-field direction + conflict behaviour. Decide
   precedence (for comms, 3CX is probably authoritative for the recording; Autotask
   stays authoritative for the customer record).

4. **Reuse the matching pipeline.** Don't write new dedupe logic — call
   `sync/matching.py`. For 3CX you'd gather Contact candidates by **phone number**
   and reuse `match_contact` (or add a phone-keyed variant), then attach the
   canonical `Communication` to the matched Contact via the same
   approval/`entity_mapping` mechanism.

5. **Register capabilities.** Insert a `connector_registry` row (the table already
   exists) describing the connector so it's discoverable without schema changes.

6. **Wire events + tests.** Publish/subscribe canonical events on Service Bus
   (config-driven routing, Spec §7.3). Add tests under `tests/` using in-process
   fakes like `tests/fakes.py` — assert the protection rules still hold (no silent
   overwrite of the system of record, before-state captured, ambiguous → approval).

## Non-negotiable rules any connector must respect (Spec §5)

- The **system of record is protected**: writes to it are gated (clean miss =
  create, fuzzy = approval, populated conflict = approval, empty = additive).
- **Capture before-state to the immutable `audit_log` before any write** to the
  system of record.
- **Never delete** records in the system of record; deletes are approval-only.
- **Idempotency**: record handled events in `processed_events`; use `entity_mapping`
  to prevent duplicate creation.
- **Environment switch**: respect `ENVIRONMENT`; never hardcode sandbox/prod;
  one environment never reads the other's credentials.

## Project conventions

- Python 3.12, FastAPI, SQLAlchemy 2.0 + Alembic. UTC timestamps everywhere.
- Engine-portable SQL only (runs on Postgres and Azure SQL) — see `db/base.py`.
- Config drives behaviour; avoid hardcoding field/stage logic in Python.
- Run `pytest` and `ruff check src` before a PR. New behaviour ships with tests.
- Secrets never enter Git — `.env` is ignored; document new vars in `.env.example`.

## Migrations

After changing models: `alembic revision --autogenerate -m "describe change"`,
review the generated script, then `alembic upgrade head`. The baseline migration
(`0001_initial`) builds the whole schema from the models; later migrations are
incremental diffs.
