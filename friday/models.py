from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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

    @property
    def conversation_id(self) -> str:
        """Where the exchange is actually happening.

        A thread is its own conversation: seeding context from the parent
        channel would pull in messages nobody in the thread was reading.
        """
        return self.thread_id or self.channel_id


@dataclass(frozen=True, slots=True)
class Session:
    """One conversation on one platform.

    Identity is (provider, channel_id, thread_id). A thread and its parent
    channel are different sessions, because they carry different context.
    """

    provider: str
    channel_id: str
    thread_id: str | None
