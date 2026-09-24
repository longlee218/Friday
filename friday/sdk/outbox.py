"""The outbox message kinds a plugin's graph queues rows as.

`Kind` is what decides whether a queued message needs approval. A plugin graph
that queues a mid-run row (an acknowledgement to the reporter, a finding to the
operator) names the `Kind` it queues as, so the contract lives in the sdk while
the delivery loop that reads it stays in `friday/outbox/`, which re-exports this.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Kind"]


class Kind(StrEnum):
    """What a message is, which is what decides whether it needs approval."""

    #: Completing the task's own required parameters — "which environment?".
    #: Not the agent speaking for the operator, so there is nothing to approve.
    ASK_FOR_DETAILS = "ask_for_details"
    #: The request for approval itself. Waiting for approval to send it would
    #: be a deadlock.
    APPROVAL_CARD = "approval_card"
    #: The agent answering in the operator's name. The only kind that waits:
    #: the risk is in answering, not in asking.
    REPLY = "reply"
    #: The system talking about itself: a connection that died, or came back.
    #: Belongs to no task.
    ALERT = "alert"
    #: The once-a-day "still here, this is what I am holding". Its own kind
    #: rather than an `ALERT`, because the two differ in the only way that
    #: matters here: an outage is reported whenever it is true, and this is
    #: reported once. Telling them apart is what lets the row itself answer
    #: "was one sent today?" — and that question has to survive a restart.
    SUMMARY = "summary"
    #: A task nobody can act on. Not a question — the operator is being told,
    #: because a task in a column nobody watches is the same as a lost one.
    HELP_WANTED = "help_wanted"
    #: "I have this and I am working on it", to the reporter, while the work
    #: runs. **Sent without approval, and that is the operator's call**
    #: (2026-09-22): an acknowledgement that waits for a person is an
    #: acknowledgement that arrives after the answer it was meant to precede.
    #: It promises nothing and concludes nothing, which is what makes it
    #: safe to send unread — the risk this queue guards is in *answering*.
    ACKNOWLEDGED = "acknowledged"
    #: An investigation finished: the cause, and where the full report is.
    #: To the operator, not to the reporter, and so not approved — it is the
    #: reading they do *before* approving the reporter's copy, and a card
    #: that waited for its own approval would be a deadlock.
    FINDING = "finding"

    @property
    def needs_approval(self) -> bool:
        return self is Kind.REPLY
