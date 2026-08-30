from __future__ import annotations

from collections.abc import Collection

from friday.models import InboundEvent, MentionType


def normalise(
    message, *, me_id: int, my_role_ids: Collection[int]
) -> InboundEvent:
    """Turn a platform message into an InboundEvent.

    Classification is deliberately ordered: a direct mention outranks a role
    mention, because being named personally is a stronger signal than being
    included in a group.
    """
    channel_id, thread_id = _location(message.channel)
    return InboundEvent(
        provider="discord",
        provider_message_id=str(message.id),
        channel_id=channel_id,
        thread_id=thread_id,
        author_id=str(message.author.id),
        author_name=message.author.display_name,
        text=message.content,
        created_at=message.created_at,
        mention_type=_mention_type(message, me_id, my_role_ids),
    )


def _mention_type(message, me_id: int, my_role_ids: Collection[int]):
    if message.guild is None:
        # Every DM is addressed to us by construction.
        return MentionType.DM
    if any(user.id == me_id for user in message.mentions):
        return MentionType.DIRECT
    if any(role.id in my_role_ids for role in message.role_mentions):
        return MentionType.ROLE
    return None


def _location(channel) -> tuple[str, str | None]:
    """A thread reports its parent as the channel and itself as the thread."""
    parent_id = getattr(channel, "parent_id", None)
    if parent_id is not None:
        return str(parent_id), str(channel.id)
    return str(channel.id), None
