"""qualify conversation ids by provider

A conversation id was the bare platform id of a thread or channel, so two
providers handing out the same number would have shared one history, one task
and one context window. It is now `provider:channel` or `provider:channel/thread`.

Autogenerate saw only the shape change to `conversations`. The data is the
actual work here, and no tool can infer it: `messages` still carries the channel
and thread the old value collapsed, and `tasks` carries neither, so its rows are
mapped through the messages they came from.

Revision ID: 2a4b94443313
Revises: 443468757024
Create Date: 2026-08-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2a4b94443313"
down_revision: str | Sequence[str] | None = "443468757024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The stored form, built in SQL so the migration does not depend on
#: application code that will keep changing after it is written.
_QUALIFIED = (
    "{provider} || ':' || {channel} || "
    "CASE WHEN {thread} IS NOT NULL AND {thread} != '' "
    "THEN '/' || {thread} ELSE '' END"
)

#: What the old value was: the thread if there was one, else the channel.
_OLD = "COALESCE(NULLIF({thread}, ''), {channel})"


def upgrade() -> None:
    messages = _QUALIFIED.format(
        provider="provider", channel="channel_id", thread="thread_id"
    )

    # Tasks first: the mapping is read from the messages a task came from, and
    # matching on the *old* value has to happen before that value is rewritten.
    op.execute(
        f"""
        UPDATE tasks SET conversation_id = (
            SELECT {messages} FROM messages m
            WHERE {_OLD.format(thread="m.thread_id", channel="m.channel_id")}
                  = tasks.conversation_id
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM messages m
            WHERE {_OLD.format(thread="m.thread_id", channel="m.channel_id")}
                  = tasks.conversation_id
        )
        """
    )
    op.execute(f"UPDATE messages SET conversation_id = {messages}")

    # SQLite cannot change a primary key in place, and the new key is a value
    # the old columns only imply — so build the table rather than alter it.
    op.create_table("conversations_new", sa.Column("id", sa.String(), primary_key=True))
    op.execute(
        "INSERT OR IGNORE INTO conversations_new (id) SELECT DISTINCT "
        + _QUALIFIED.format(
            provider="provider", channel="channel_id", thread="thread_id"
        )
        + " FROM conversations"
    )
    op.drop_table("conversations")
    op.rename_table("conversations_new", "conversations")


def downgrade() -> None:
    op.create_table(
        "conversations_old",
        sa.Column("provider", sa.String(), primary_key=True),
        sa.Column("channel_id", sa.String(), primary_key=True),
        sa.Column("thread_id", sa.String(), primary_key=True, server_default=""),
    )
    op.execute(
        """
        INSERT OR IGNORE INTO conversations_old (provider, channel_id, thread_id)
        SELECT
            substr(id, 1, instr(id, ':') - 1),
            CASE WHEN instr(id, '/') > 0
                 THEN substr(id, instr(id, ':') + 1,
                             instr(id, '/') - instr(id, ':') - 1)
                 ELSE substr(id, instr(id, ':') + 1) END,
            CASE WHEN instr(id, '/') > 0
                 THEN substr(id, instr(id, '/') + 1) ELSE '' END
        FROM conversations
        """
    )
    op.drop_table("conversations")
    op.rename_table("conversations_old", "conversations")

    # Tasks before messages again, for the same reason and in the other
    # direction: the join matches on the value `messages` is about to lose.
    old = _OLD.format(thread="m.thread_id", channel="m.channel_id")
    op.execute(
        f"""
        UPDATE tasks SET conversation_id = (
            SELECT {old} FROM messages m
            WHERE m.conversation_id = tasks.conversation_id LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM messages m
            WHERE m.conversation_id = tasks.conversation_id
        )
        """
    )
    op.execute(
        "UPDATE messages SET conversation_id = "
        + _OLD.format(thread="thread_id", channel="channel_id")
    )
