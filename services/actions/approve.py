"""Part 6: Priya's side of an approval. NOT an MCP tool: the model can request an
approval (request_approval) but can never grant one.

grant() is called from Priya's authenticated session (a channel.Session from
channel.login). It issues the kit's HMAC token for the exact stored payload,
valid for approvals.ttl_minutes from params.yaml, and records approval_granted
with the payload hash only.
"""
from creditwatch import approvals


class ApprovalRefused(Exception):
    """The grant itself is refused: unknown draft, no request, wrong mandate, or already used."""


def grant(store, draft_id, approver_session, audit, now, ttl_min):
    payload = store.get(draft_id)
    if payload is None:
        raise ApprovalRefused(f"no pending draft {draft_id!r}")
    if draft_id not in store.requested:
        raise ApprovalRefused(f"{draft_id!r} has no approval request")
    if draft_id in store.sent:
        raise ApprovalRefused(f"{draft_id!r} was already used")
    if payload["scope"] != approver_session.mandate:
        raise ApprovalRefused(f"{draft_id!r} belongs to another mandate's session")
    token = approvals.issue_token(payload, approver_session.principal, now=now, ttl_min=ttl_min)
    audit.record("approval_granted", approver=approver_session.principal, draft_id=draft_id,
                 payload_hash=approvals.payload_hash(payload))
    return token
