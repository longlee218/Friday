from __future__ import annotations

from collections.abc import Collection

from friday.kernel.domain.models import InboundEvent, MentionType
from friday.text.transform import Attachment, render_attachments, transform


def normalise(
    message, *, me_id: int, my_role_ids: Collection[int], is_own: bool = False
) -> InboundEvent:
    """Turn a platform message into an InboundEvent.

    Classification is deliberately ordered: a direct mention outranks a role
    mention, because being named personally is a stronger signal than being
    included in a group.
    """
    channel_id, thread_id = _location(message.channel)
    attachments = tuple(
        Attachment(filename=a.filename, content_type=getattr(a, "content_type", None))
        for a in getattr(message, "attachments", ()) or ()
    )
    # clean_content resolves <@1234> into readable names. The raw markup is
    # noise to a model, and worse in the tone examples the responder learns
    # from. `transform` takes it from there: prose cleaned, code untouched.
    cleaned = transform(getattr(message, "clean_content", None) or message.content)
    said = "\n\n".join(
        part for part in (cleaned.text, render_attachments(attachments)) if part
    )
    return InboundEvent(
        provider="discord",
        provider_message_id=str(message.id),
        channel_id=channel_id,
        thread_id=thread_id,
        author_id=str(message.author.id),
        author_name=message.author.display_name,
        text=said,
        created_at=message.created_at,
        mention_type=_mention_type(message, me_id, my_role_ids),
        is_own=is_own,
        reply_to=_reply_to(message),
        code=cleaned.code,
        attachments=attachments,
    )


def _reply_to(message) -> str | None:
    reference = getattr(message, "reference", None)
    message_id = getattr(reference, "message_id", None)
    return str(message_id) if message_id is not None else None


def _mention_type(message, me_id: int, my_role_ids: Collection[int]):
    if _is_one_to_one_dm(message.channel):
        return MentionType.DM
    if any(user.id == me_id for user in message.mentions):
        return MentionType.DIRECT
    if any(role.id in my_role_ids for role in message.role_mentions):
        return MentionType.ROLE
    return None


def _is_one_to_one_dm(channel) -> bool:
    """True only for a private conversation with a single other person.

    Every message in one is addressed to us by construction, so no mention is
    needed. A *group* chat is deliberately not included: most of its traffic is
    not for us, so it behaves like a channel — it must be whitelisted and must
    actually mention us. The library gives a one-to-one DM a single `recipient`
    and a group one `recipients`.
    """
    return getattr(channel, "recipient", None) is not None


def _location(channel) -> tuple[str, str | None]:
    """A thread reports its parent as the channel and itself as the thread."""
    parent_id = getattr(channel, "parent_id", None)
    if parent_id is not None:
        return str(parent_id), str(channel.id)
    return str(channel.id), None
