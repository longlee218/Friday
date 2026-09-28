"""What arrives: a message addressed to the watched account, and the verbatim
material it carried."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.kernel.domain.conversation import ConversationId, resolve


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
    #: The `provider_message_id` this replies to, if it is a reply. One of the
    #: three structural signals a relevant-context filter reads — the other two
    #: are `mention_type` and `is_own` itself.
    reply_to: str | None = None
    #: Set by the store when the message opened a task (ticket 11).
    #: `None` is the ordinary case; a value means the operator's Rooms
    #: screen should mark this message with a task glyph.
    task_id: int | None = None
    #: Set by the store when an agent wrote a memory while processing this
    #: message (ticket 11). The Rooms screen marks these with an
    #: enrichment glyph so the operator can find what the agent decided
    #: was worth remembering.
    is_enrichment: bool = False
    #: Code the message carried, verbatim: a curl, a stack trace, a payload.
    #: Already inside `text` too — this is the same content addressable as
    #: itself, for anything that wants the code without the prose around it.
    code: tuple[str, ...] = ()
    #: Files posted with the message. Named, never fetched.
    attachments: tuple = ()

    @property
    def conversation(self) -> ConversationId:
        """Where the exchange is happening. Delegated, never derived here —
        the rules live in one module so a platform that threads differently can
        be added without every caller learning about it."""
        return resolve(self)


@dataclass(frozen=True, slots=True)
class Artifact:
    """Verbatim material a message carried — code, a stack trace, SQL, a log,
    a `curl` — stored whole and pointed at rather than paraphrased (board
    `what-the-room-already-knows`, ticket 07, D8).

    `content` is exactly what `friday.kernel.text_transform.transform` lifted out
    of the message that produced it, untouched since. A build that needs it
    back gets `content` byte for byte; a build that must never see it — the
    summariser — gets `id` and `description` only, through
    `friday.kernel.text_transform.redact`.

    `id` is opaque and sparse, the same reasoning as `Memory.id`: a model
    that invents one fails rather than landing on somebody else's artifact.

    No `deleted_at`, unlike `Memory` — nothing writes an artifact except the
    store itself, at the moment a message that carries code is first
    recorded, so there is no agent decision to take back.
    """

    id: str
    channel_id: str
    provider: str
    source_message_id: str
    content: str
    description: str
    created_at: datetime
