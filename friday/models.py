from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from enum import StrEnum

from friday.conversation import ConversationId, resolve


class MentionType(StrEnum):
    """How the watched account was addressed."""

    DIRECT = "direct"
    ROLE = "role"
    DM = "dm"


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """A message addressed to the watched account, normalised by a provider.

    `mention_type` is None when the provider saw the message but the watched
    account was not addressed in it.
    """

    provider: str
    provider_message_id: str
    channel_id: str
    thread_id: str | None
    author_id: str
    author_name: str
    text: str
    created_at: datetime
    mention_type: MentionType | None
    #: Written by the watched account. Reported, not interpreted: whether it
    #: means "ignore" is the inbox's decision, and the responder needs these as
    #: examples of how the operator actually writes.
    is_own: bool = False

    @property
    def conversation(self) -> ConversationId:
        """Where the exchange is happening. Delegated, never derived here —
        the rules live in one module so a platform that threads differently can
        be added without every caller learning about it."""
        return resolve(self)


@dataclass(frozen=True, slots=True)
class Task:
    """A piece of work derived from a mention.

    `state` is a plain string here; the legal transitions between states are
    ticket 05's concern. Triage only ever creates a task in one of two: ready to
    work on, or waiting on a human.
    """

    id: int
    conversation: ConversationId
    type: str
    state: str
    confidence: float
    params: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Outbound:
    """Something to send, held as data rather than performed as a call.

    `kind` decides whether it needs approval; `sender` decides which identity
    says it. Approval itself is a fact about the task, not about this row.
    """

    id: int
    task_id: int
    conversation: ConversationId
    kind: str
    sender: str
    text: str
    reply_to: str | None = None
    state: str = "queued"
    attempts: int = 0
    last_error: str | None = None


# ---- task parameters -------------------------------------------------

"""What each kind of task carries.

Here rather than in `friday.triage`, which is where they were: a workflow
reached for the schema of a task's parameters *through the agent that happens
to fill them in*. The types describe the work, not the thing that recognised
it — and their annotations are read directly to decide what a task cannot
proceed without.
"""

TaskType = Literal["api_issue", "access_request", "doc_question", "skip"]


@dataclass(frozen=True, slots=True)
class ApiIssueParams:
    summary: str
    environment: str | None = None
    correlation_id: str | None = None
    curl: str | None = None


@dataclass(frozen=True, slots=True)
class AccessRequestParams:
    project: str
    permission: str
    summary: str


@dataclass(frozen=True, slots=True)
class DocQuestionParams:
    question: str
    doc_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SkipParams:
    reason: str


Params = ApiIssueParams | AccessRequestParams | DocQuestionParams | SkipParams
