"""Adaptive Card builders for the Teams Ops Bot (Spec §11.1).

Every ambiguous change posts a card showing source, entity link, and before/after
values, with **Approve / Reject / Override** actions that call back into the
integration API. The callback carries the approval id + a verification token so a
spoofed callback cannot approve a change (Spec §11.1 "verify the caller").
"""

from __future__ import annotations

from ..db.models import ApprovalQueue

_SEVERITY_COLOR = {"low": "good", "med": "warning", "high": "attention"}


def _fact(title: str, value: object) -> dict:
    return {"title": title, "value": str(value)}


def approval_card(approval: ApprovalQueue, callback_token: str) -> dict:
    """Build an Adaptive Card (v1.5) for a pending approval."""
    pc = approval.proposed_change or {}
    body: list[dict] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": f"Approval needed — {approval.approval_type}",
            "color": _SEVERITY_COLOR.get(str(approval.severity), "default"),
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "spacing": "None",
            "isSubtle": True,
            "wrap": True,
            "text": f"Severity {str(approval.severity).upper()} · {approval.source_system} → "
            f"{approval.target_system} · env {approval.environment}",
        },
        {
            "type": "FactSet",
            "facts": [
                _fact("Approval #", approval.id),
                _fact("Entity", approval.canonical_entity_type),
                _fact("Autotask id", approval.autotask_id or "—"),
                _fact("GHL id", approval.ghl_id or "—"),
                _fact("Correlation", approval.correlation_id),
            ],
        },
        {"type": "TextBlock", "weight": "Bolder", "text": "Reason", "wrap": True},
        {"type": "TextBlock", "text": approval.detected_reason, "wrap": True},
    ]

    # Field-conflict cards show a before/after table (Spec §11.1).
    if "fields" in pc:
        body.append({"type": "TextBlock", "weight": "Bolder", "text": "Proposed changes", "wrap": True})
        for f in pc["fields"]:
            body.append(
                {
                    "type": "TextBlock",
                    "wrap": True,
                    "text": f"**{f['field']}** ({f.get('severity', '')}):  "
                    f"`{f.get('before')}`  →  `{f.get('after')}`",
                }
            )
    # Flow-2 single-field cards (sales outcome / amount conflict, Spec §10.2).
    if "field" in pc and "fields" not in pc:
        body.append(
            {
                "type": "TextBlock",
                "wrap": True,
                "text": f"**{pc['field']}**:  `{pc.get('before')}`  →  `{pc.get('after')}`",
            }
        )
    # Stage-C onboarding cards show the won deal + its value (Spec §8.2, §11.1).
    if "deal" in pc:
        deal = pc["deal"] or {}
        body.append(
            {
                "type": "TextBlock",
                "weight": "Bolder",
                "wrap": True,
                "text": f"Won deal: {deal.get('name', '—')}  ·  "
                f"value {deal.get('monetary_value', '—')}",
            }
        )
    for key, label in (
        ("candidates", "Candidates"),
        ("contact_candidates", "Possible existing Contacts"),
        ("account_candidates", "Possible Accounts to link"),
    ):
        if pc.get(key):
            body.append({"type": "TextBlock", "weight": "Bolder", "text": label, "wrap": True})
            for c in pc[key]:
                if isinstance(c, dict):
                    label_bits = [str(c.get("id") or c.get("source_id") or "?")]
                    name = c.get("name") or " ".join(
                        p for p in (c.get("first_name"), c.get("last_name")) if p
                    )
                    if name:
                        label_bits.append(name)
                    if c.get("email"):
                        label_bits.append(str(c["email"]))
                    text = " · ".join(label_bits)
                else:
                    text = str(c)
                body.append({"type": "TextBlock", "wrap": True, "text": f"• {text}"})

    # Buttons call back into POST /approvals/{id}/decide with the verification token.
    base_data = {"approval_id": approval.id, "token": callback_token}
    actions = [
        {"type": "Action.Submit", "title": "✅ Approve", "data": {**base_data, "decision": "approve"}},
        {"type": "Action.Submit", "title": "❌ Reject", "data": {**base_data, "decision": "reject"}},
    ]
    # Override (pick a candidate) is offered where the approval carries candidates.
    if pc.get("candidates") or pc.get("account_candidates") or pc.get("contact_candidates"):
        actions.append(
            {
                "type": "Action.ShowCard",
                "title": "✏️ Override (pick)",
                "card": {
                    "type": "AdaptiveCard",
                    "body": [
                        {
                            "type": "Input.Text",
                            "id": "chosen_id",
                            "label": "Chosen Autotask Account/Contact id",
                        }
                    ],
                    "actions": [
                        {
                            "type": "Action.Submit",
                            "title": "Apply override",
                            "data": {**base_data, "decision": "override"},
                        }
                    ],
                },
            }
        )

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": body,
        "actions": actions,
    }
