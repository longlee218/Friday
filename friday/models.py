from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from enum import StrEnum


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
    def conversation_id(self) -> str:
        """Where the exchange is actually happening.

        A thread is its own conversation: seeding context from the parent
        channel would pull in messages nobody in the thread was reading.
        """
        return self.thread_id or self.channel_id


@dataclass(frozen=True, slots=True)
class Conversation:
    """Where an exchange is happening: a channel, a thread, or a DM.

    Identity is (provider, channel_id, thread_id). A thread and its parent
    channel are different conversations, because they carry different context.

    Not called `Session`: the Agents SDK uses that word for a transcript of an
    agent's own turns, which is a different thing entirely.
    """

    provider: str
    channel_id: str
    thread_id: str | None


@dataclass(frozen=True, slots=True)
class Task:
    """A piece of work derived from a mention.

    `state` is a plain string here; the legal transitions between states are
    ticket 05's concern. Triage only ever creates a task in one of two: ready to
    work on, or waiting on a human.
    """

    id: int
    conversation_id: str
    type: str
    state: str
    confidence: float
    params: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
