"""What leaves: an outbound row, the digest its approval freezes, and the
audit line that records it."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from friday.kernel.domain.conversation import ConversationId


@dataclass(frozen=True, slots=True)
class Outbound:
    """Something to send, held as data rather than performed as a call.

    `kind` decides whether it needs approval; `sender` decides which identity
    says it. Approval itself is a fact about this row, not about its task —
    approving one reply must not approve the next.
    """

    id: int
    task_id: int | None
    conversation: ConversationId
    kind: str
    sender: str
    text: str
    reply_to: str | None = None
    state: str = "queued"
    attempts: int = 0
    last_error: str | None = None
    #: On an approval card, the id of the row it asks about.
    approves: int | None = None
    #: The message that was approved, frozen as a hash. Set when the row becomes
    #: sendable — at enqueue for a kind approved by policy, at approval for a
    #: reply the operator releases — and checked again at dispatch: if the text
    #: has changed since, the approval no longer describes this message and is
    #: void. `None` on a row that is not yet sendable.
    approved_payload_hash: str | None = None


#: The provenance written into an outbound row's `approved_by` when its kind
#: needs no operator approval: it was released by policy at enqueue, not by a
#: person. Kept apart from an operator name so the audit trail says which
#: released it.
POLICY = "policy"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One line of the append-only audit log (DESIGN-v2 §12).

    A fact that happened, never edited: who approved which bytes, a plugin
    loaded with its trust tier, an MCP server's tool grant, a refused decision.
    `event` is the kind, `actor` is who (or `None` for the system), and `detail`
    carries the specifics the reader needs — the outbound id and payload hash of
    an approval, the tool list of a grant. The kernel only ever appends these
    (`friday.kernel.audit`); nothing updates or deletes a row.
    """

    id: int
    at: datetime
    event: str
    actor: str | None
    detail: dict[str, Any]


def payload_hash(
    *,
    kind: str,
    sender: str,
    conversation: ConversationId | str,
    text: str,
    reply_to: str | None,
) -> str:
    """The message that was approved, as one stable digest.

    What "approval" is a fact *about*: the words, who says them, where they go
    and what they answer. Frozen when the row becomes sendable and recomputed at
    dispatch, so a text edited after approval no longer matches and the approval
    is void — the check that stops an approved-then-changed reply going out
    unread. A closed tuple, joined with a separator no field can contain, so two
    different rows cannot hash the same by concatenation.

    In `domain` rather than `outbox` so the store can freeze the hash at enqueue
    and approval without importing the module that reads it back at dispatch.
    """
    parts = [kind, sender, str(conversation), text, reply_to or ""]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def payload_hash_of(row: Outbound) -> str:
    """`payload_hash` for a row already in hand — the two sites that hash an
    existing `Outbound` (freezing it at approval, checking it at dispatch) share
    one field list, so a new hashed field is added in one place, not two."""
    return payload_hash(
        kind=row.kind,
        sender=row.sender,
        conversation=row.conversation,
        text=row.text,
        reply_to=row.reply_to,
    )
