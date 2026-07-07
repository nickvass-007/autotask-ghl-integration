"""Sync Profiles — saved criteria + dry-run approval workflow (portal).

A profile is a saved audience definition. NOTHING live runs from a profile
until its safety gates pass:

1. ``dry_run_required``: a dry-run must have completed for the CURRENT
   criteria (hash-matched — editing criteria invalidates prior runs).
2. ``requires_approval_before_live_sync``: an operator must approve after
   reviewing the dry-run.
3. Significant-change detection: if a new dry-run differs materially from the
   previous one (±20% customers, conflicts up, >10 new creates, a previously
   excluded customer type appearing), the profile flips to ``review_required``
   and scheduled live syncs pause until re-approved.

Live runs re-use the same per-contact pipeline as the poller/backfill
(push_autotask_contact), record every action into the transaction feed under
the job's correlation id, honour cancellation, and never overlap per profile.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config.settings import get_settings
from ..core.logging import get_logger, new_correlation_id
from ..db.base import utcnow
from ..db.enums import CanonicalEntityType, Environment
from ..db.models import (
    EntityMapping,
    PortalSetting,
    SyncJob,
    SyncProfile,
    SyncProfileSnapshot,
)
from .autotask_to_ghl import push_autotask_contact
from .companies import company_mapping_by_autotask
from .criteria import matches

log = get_logger(__name__)

# ── Portal settings (DB-backed, env-scoped) ──────────────────────────────────
SETTING_DEFAULTS: dict[str, str] = {
    "default_timezone": "Australia/Sydney",
    "default_dry_run_required": "true",
    "require_approval_before_live_sync": "true",
    "max_customers_per_sync": "1000",
    "max_contacts_per_sync": "10000",
    "conflict_threshold": "25",
    "warning_threshold": "100",
    "error_threshold": "50",
    "allow_live_scheduled_syncs": "true",
    "allow_weekend_schedules": "true",
    "allow_overnight_schedules": "true",
    # Autotask web UI base for deep links (zone host), e.g. https://ww29.autotask.net
    "autotask_web_base": "https://ww29.autotask.net",
    # Placeholders — no notification infra exists yet (documented limitation).
    "notify_email": "",
    "notify_webhook_url": "",
}


def get_portal_settings(session: Session) -> dict[str, str]:
    env = get_settings().environment
    rows = session.execute(
        select(PortalSetting).where(PortalSetting.environment == env)
    ).scalars()
    out = dict(SETTING_DEFAULTS)
    out.update({r.key: r.value for r in rows})
    return out


def set_portal_setting(session: Session, key: str, value: str) -> None:
    if key not in SETTING_DEFAULTS:
        raise ValueError(f"unknown setting {key!r}")
    env = get_settings().environment
    row = session.execute(
        select(PortalSetting).where(
            PortalSetting.environment == env, PortalSetting.key == key
        )
    ).scalar_one_or_none()
    if row is None:
        row = PortalSetting(environment=env, key=key, value=str(value))
        session.add(row)
    else:
        row.value = str(value)
        row.updated_at = utcnow()
    session.flush()


# ── Criteria ──────────────────────────────────────────────────────────────────
def criteria_hash(criteria: dict) -> str:
    return hashlib.sha256(json.dumps(criteria or {}, sort_keys=True).encode()).hexdigest()


def _rules_from_criteria(criteria: dict) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=i, field=r["field"], operator=r.get("operator", "eq"), value=str(r["value"])
        )
        for i, r in enumerate(criteria.get("rules", []))
    ]


# ── Dry run ───────────────────────────────────────────────────────────────────
async def _fetch_all_companies(autotask) -> list[dict]:
    """Page through every Company (id cursor, 500/page)."""
    out: list[dict] = []
    last_id = 0
    while True:
        resp = await autotask._client.post(
            autotask._url("Companies/query"),
            headers=autotask._auth_headers(),
            json={"filter": [{"op": "gt", "field": "id", "value": last_id}], "MaxRecords": 500},
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            break
        out.extend(items)
        last_id = max(int(i["id"]) for i in items)
        if len(items) < 500:
            break
    return out


async def _active_contact_count(autotask, account_ids: list[int]) -> int:
    total = 0
    for i in range(0, len(account_ids), 100):
        chunk = account_ids[i : i + 100]
        resp = await autotask._client.post(
            autotask._url("Contacts/query/count"),
            headers=autotask._auth_headers(),
            json={
                "filter": [
                    {"op": "in", "field": "companyID", "value": chunk},
                    {"op": "eq", "field": "isActive", "value": True},
                ]
            },
        )
        resp.raise_for_status()
        total += resp.json().get("queryCount", 0)
    return total


def _significant_change(prev: SyncProfileSnapshot | None, new_summary: dict) -> str | None:
    """Spec §6: reasons a new dry-run needs human review; None = not significant."""
    if prev is None:
        return None
    old = prev.summary_json or {}
    old_matched = old.get("customers_matched", 0)
    new_matched = new_summary.get("customers_matched", 0)
    if old_matched and abs(new_matched - old_matched) / max(old_matched, 1) > 0.20:
        return f"customer count changed {old_matched} → {new_matched} (>20%)"
    if new_summary.get("conflicts", 0) > (old.get("conflicts", 0)):
        return "conflict count increased since last dry-run"
    if new_summary.get("customers_new", 0) - old.get("customers_new", 0) > 10:
        return "more than 10 additional new customers would be created"
    old_types = {t["type"] for t in old.get("type_breakdown", []) if t.get("action") == "Sync"}
    new_types = {t["type"] for t in new_summary.get("type_breakdown", []) if t.get("action") == "Sync"}
    added = new_types - old_types
    if added:
        return f"previously excluded customer type(s) now included: {', '.join(sorted(added))}"
    return None


async def run_dry_run(
    session: Session, *, profile: SyncProfile, autotask, started_by: str, trigger: str = "manual"
) -> SyncJob:
    """Compute what a live run WOULD do; store the job + snapshot; apply the
    significant-change safety rules. Writes nothing to GHL or Autotask."""
    env = get_settings().environment
    job = SyncJob(
        environment=env,
        profile_id=profile.id,
        kind="dry_run",
        trigger=trigger,
        started_by=started_by,
        status="running",
        started_at=utcnow(),
        correlation_id=new_correlation_id(),
    )
    session.add(job)
    session.flush()

    criteria = profile.criteria_json or {}
    rules = _rules_from_criteria(criteria)
    chash = criteria_hash(criteria)
    selected = set((profile.selected_customer_ids or {}).get("ids", []))

    type_labels = await autotask.get_picklist_labels("Companies", "companyType")
    class_labels = await autotask.get_picklist_labels("Companies", "classification")
    companies = await _fetch_all_companies(autotask)

    matched: list[dict] = []
    excluded: list[dict] = []
    warnings = 0
    for raw in companies:
        cid = int(raw["id"])
        if selected and cid not in selected:
            continue  # explicit include-list: everything else is out of scope
        if matches(raw, rules):
            matched.append(raw)
            if not raw.get("companyName"):
                warnings += 1
        else:
            reasons = []
            for r in rules:
                if not matches(raw, [r]):
                    reasons.append(f"{r.field} {r.operator} {r.value}")
            excluded.append({"id": cid, "reasons": reasons, "raw": raw})

    matched_ids = [int(m["id"]) for m in matched]
    linked_ids = {
        m["id"]
        for m in matched
        if company_mapping_by_autotask(session, str(m["id"])) is not None
    }
    contact_total = await _active_contact_count(autotask, matched_ids) if matched_ids else 0

    def _bucket(rows: list[dict], action: str) -> list[dict]:
        buckets: dict[tuple[str, str], int] = {}
        for r in rows:
            raw = r.get("raw", r)
            t = type_labels.get(str(raw.get("companyType")), str(raw.get("companyType")))
            c = class_labels.get(str(raw.get("classification")), str(raw.get("classification") or "—"))
            buckets[(t, c)] = buckets.get((t, c), 0) + 1
        return [
            {"type": t, "classification": c, "count": n, "action": action}
            for (t, c), n in sorted(buckets.items(), key=lambda kv: -kv[1])
        ]

    summary = {
        "customers_matched": len(matched),
        "customers_linked": len(linked_ids),
        "customers_new": len(matched) - len(linked_ids),
        "customers_excluded": len(excluded),
        "contacts_matched": contact_total,
        "contacts_already_synced": session.query(EntityMapping)
        .filter_by(environment=env, canonical_entity_type=CanonicalEntityType.CONTACT)
        .count(),
        "warnings": warnings,
        "conflicts": 0,  # AT→GHL contact mirror has no conflict path by design (§5.1)
        "skipped": len(excluded),
        "type_breakdown": _bucket(matched, "Sync") + _bucket(excluded, "Excluded"),
        "excluded_examples": [
            {"id": e["id"], "name": e["raw"].get("companyName"), "reasons": e["reasons"]}
            for e in excluded[:25]
        ],
        "matched_preview": [
            {
                "id": int(m["id"]),
                "name": m.get("companyName"),
                "type": type_labels.get(str(m.get("companyType")), str(m.get("companyType"))),
                "classification": class_labels.get(
                    str(m.get("classification")), str(m.get("classification") or "—")
                ),
                "linked": int(m["id"]) in {int(i) for i in linked_ids},
            }
            for m in matched[:500]
        ],
    }

    prev = session.execute(
        select(SyncProfileSnapshot)
        .where(
            SyncProfileSnapshot.environment == env,
            SyncProfileSnapshot.profile_id == profile.id,
        )
        .order_by(SyncProfileSnapshot.created_at.desc())
    ).scalars().first()

    snapshot = SyncProfileSnapshot(
        environment=env,
        profile_id=profile.id,
        job_id=job.id,
        criteria_hash=chash,
        criteria_snapshot=criteria,
        matched_customer_ids={"ids": matched_ids},
        matched_contact_count=contact_total,
        excluded_ids={"ids": [e["id"] for e in excluded]},
        summary_json=summary,
        warnings=warnings,
        conflicts=0,
    )
    session.add(snapshot)

    job.status = "succeeded"
    job.ended_at = utcnow()
    job.summary_json = summary
    profile.last_dry_run_job_id = job.id
    profile.criteria_hash = chash
    profile.last_run_at = utcnow()

    reason = _significant_change(prev, summary)
    if reason and profile.review_state == "approved":
        profile.review_state = "review_required"
        profile.review_reason = reason
        profile.schedule_paused = True
        log.warning("Profile %s paused for review: %s", profile.name, reason)
        from .notify import notify

        await notify(session, "Paused for review", f"{profile.name}: {reason}")
    elif profile.review_state in ("dry_run_required", "review_required"):
        profile.review_state = (
            "pending" if profile.requires_approval_before_live_sync else "approved"
        )
        profile.review_reason = reason
    session.flush()
    return job


def approve_profile(session: Session, profile: SyncProfile, *, approved_by: str) -> None:
    """Operator sign-off after reviewing the dry-run (§6). Requires a dry-run
    for the CURRENT criteria."""
    snap = latest_snapshot(session, profile)
    if snap is None or snap.criteria_hash != criteria_hash(profile.criteria_json or {}):
        raise ValueError("A dry-run for the current criteria is required before approval")
    profile.review_state = "approved"
    profile.review_reason = None
    profile.approved_at = utcnow()
    profile.approved_by = approved_by
    profile.schedule_paused = False
    session.flush()


def latest_snapshot(session: Session, profile: SyncProfile) -> SyncProfileSnapshot | None:
    env = get_settings().environment
    return session.execute(
        select(SyncProfileSnapshot)
        .where(
            SyncProfileSnapshot.environment == env,
            SyncProfileSnapshot.profile_id == profile.id,
        )
        .order_by(SyncProfileSnapshot.created_at.desc())
    ).scalars().first()


def live_run_blockers(session: Session, profile: SyncProfile) -> list[str]:
    """Why a live run may not start right now. Empty list = clear to run."""
    blockers: list[str] = []
    if not profile.enabled:
        blockers.append("profile is disabled")
    snap = latest_snapshot(session, profile)
    current_hash = criteria_hash(profile.criteria_json or {})
    if profile.dry_run_required:
        if snap is None:
            blockers.append("dry-run required — none has been run")
        elif snap.criteria_hash != current_hash:
            blockers.append("criteria changed since the last dry-run — run a new dry-run")
    if profile.requires_approval_before_live_sync and profile.review_state != "approved":
        blockers.append(f"operator approval required (state: {profile.review_state})")
    if profile.review_state == "review_required":
        blockers.append(f"paused for review: {profile.review_reason}")
    settings = get_portal_settings(session)
    if snap is not None:
        if len((snap.matched_customer_ids or {}).get("ids", [])) > int(
            settings["max_customers_per_sync"]
        ):
            blockers.append("matched customers exceed max_customers_per_sync")
        if snap.matched_contact_count > int(settings["max_contacts_per_sync"]):
            blockers.append("matched contacts exceed max_contacts_per_sync")
    running = session.execute(
        select(SyncJob).where(
            SyncJob.environment == get_settings().environment,
            SyncJob.profile_id == profile.id,
            SyncJob.status == "running",
        )
    ).scalars().first()
    if running is not None:
        blockers.append(f"job #{running.id} is already running for this profile")
    return blockers


async def run_live(
    session: Session,
    *,
    profile: SyncProfile,
    autotask,
    ghl,
    started_by: str,
    trigger: str = "manual",
) -> SyncJob:
    """Execute the profile's audience through the standard gated contact
    pipeline. Cancellable; per-item commits; never overlaps per profile."""
    blockers = live_run_blockers(session, profile)
    if blockers:
        raise ValueError("; ".join(blockers))
    env = get_settings().environment
    snap = latest_snapshot(session, profile)
    account_ids = list((snap.matched_customer_ids or {}).get("ids", []))

    job = SyncJob(
        environment=env,
        profile_id=profile.id,
        kind="live",
        trigger=trigger,
        started_by=started_by,
        status="running",
        started_at=utcnow(),
        correlation_id=new_correlation_id(),
    )
    session.add(job)
    profile.last_live_sync_job_id = None  # set on completion
    session.commit()

    pushed = skipped = errors = 0
    error_threshold = int(get_portal_settings(session)["error_threshold"])
    try:
        for idx, account_id in enumerate(account_ids):
            session.expire_all()
            fresh = session.get(SyncJob, job.id)
            if fresh.cancel_requested:
                job.status = "cancelled"
                break
            resp = await autotask._client.post(
                autotask._url("Contacts/query"),
                headers=autotask._auth_headers(),
                json={
                    "filter": [
                        {"op": "eq", "field": "companyID", "value": int(account_id)},
                        {"op": "eq", "field": "isActive", "value": True},
                    ],
                    "MaxRecords": 500,
                },
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                contact = autotask._to_canonical(item)
                try:
                    action = await push_autotask_contact(
                        session, at_contact=contact, ghl=ghl,
                        correlation_id=job.correlation_id,
                    )
                    session.commit()
                    if action in ("skipped_duplicate",):
                        skipped += 1
                    else:
                        pushed += 1
                except Exception as exc:  # keep going; item is in the feed
                    session.rollback()
                    errors += 1
                    log.warning("Live sync %s: contact %s failed: %s", job.id, item.get("id"), exc)
            if errors > error_threshold:
                job.status = "failed"
                job.error = f"error threshold exceeded ({errors} > {error_threshold})"
                break
        else:
            job.status = "succeeded"
    except Exception as exc:
        session.rollback()
        job.status = "failed"
        job.error = str(exc)

    job.ended_at = utcnow()
    job.summary_json = {
        "accounts": len(account_ids),
        "contacts_pushed": pushed,
        "contacts_skipped": skipped,
        "errors": errors,
    }
    profile.last_live_sync_job_id = job.id
    profile.last_run_at = utcnow()
    session.commit()
    from .notify import notify

    await notify(
        session,
        f"Live sync {job.status}",
        f"{profile.name}: pushed {pushed}, skipped {skipped}, errors {errors}"
        + (f" — {job.error}" if job.error else ""),
    )
    return job
