"""The application identity, whose only job is to interrupt the operator.

It does that for exactly two reasons: something needs approving, and something
cannot be handled. Asking a reporter for a correlationId is neither — the agent
decides that itself, because the risk in this system is in *answering*, not in
asking.

Buttons are an application-only Discord feature — a user account cannot send
message components at all — so approval has to flow through a bot. That is also
the better place for it: the interaction gives a record of who clicked and when,
through the sanctioned API, rather than a parsed reply.

**It sends nothing else.** Everything the outside world sees comes from the user
account; this identity exists so the operator can be asked a question. Verified
against the live account before this was written: it reaches the operator by
direct message while sharing no guild with them, and needs no privileged
intents.

Kept apart from the user client in the same way and for the opposite reason —
that one depends on a private API and is expected to break; this one is
supported, and mixing them would hide which is which.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import discord

from friday.models import Outbound
from friday.outbox import Kind

__all__ = ["DiscordBot"]

log = logging.getLogger(__name__)

#: Namespaced so a button from anything else is visibly not ours.
PREFIX = "friday"


class DiscordBot:
    """Interrupts the operator, and reports what they said back."""

    name = "discord"

    def __init__(
        self,
        token: str,
        *,
        operator_id: int,
        client=None,
        board_url: str | None = None,
        on_decision: Callable[..., object] | None = None,
    ) -> None:
        self._token = token
        self._operator_id = operator_id
        self._board_url = board_url
        # No privileged intents: this identity reads nothing. It sends a direct
        # message and receives the interaction that answers it.
        self._client = client or discord.Client(intents=discord.Intents.none())
        self._on_decision = on_decision

    async def send(self, row: Outbound) -> str | None:
        """Direct-message the operator about one row.

        Two things arrive here and they are not the same. A proposed reply is a
        *question* — it carries buttons, and what it proposes is not public
        until it is answered. Being stuck is a *statement*: there is no decision
        to make, and buttons on it would be a question the operator has to work
        out the meaning of.
        """
        user = await self._client.fetch_user(self._operator_id)
        channel = user.dm_channel or await user.create_dm()
        asking = row.kind == Kind.APPROVAL_CARD
        message = await channel.send(
            content=_asking(row) if asking else _stuck(row, self._board_url),
            view=_Buttons(row.task_id, self.handle) if asking else None,
        )
        log.info("%s the operator about task %d",
                 "asked" if asking else "told", row.task_id)
        return str(message.id)

    async def handle(self, custom_id: str, *, by: str) -> None:
        """Report a decision. Applying it is the caller's business."""
        parts = custom_id.split(":")
        if len(parts) != 3 or parts[0] != PREFIX:
            return
        _, decision, task_id = parts
        if decision not in ("approve", "reject") or self._on_decision is None:
            return
        result = self._on_decision(
            task_id=int(task_id), approved=decision == "approve", by=by
        )
        if hasattr(result, "__await__"):
            await result

    async def start(self) -> None:
        """Connect, and re-register the buttons.

        A decision may be made hours after the question, across a restart. The
        task id lives in the button rather than in this process, and the view is
        registered again so old cards keep working.
        """
        self._client.add_view(_Buttons(None, self.handle))
        await self._client.start(self._token)


def _asking(row: Outbound) -> str:
    """Plain text rather than an embed: the thing being approved is a chat
    message, and it should be read as it will be sent."""
    return (
        f"**Reply to {row.conversation}?**\n"
        f"> {row.text}\n"
        f"_task {row.task_id} · goes out as you_"
    )


def _stuck(row: Outbound, board_url: str | None) -> str:
    """Enough to judge without opening anything, and where to go if you want to."""
    where = f"\n{board_url}" if board_url else ""
    return f"**Nothing I can do with this**\n> {row.text}\n_in {row.conversation}_{where}"


class _Buttons(discord.ui.View):
    """`timeout=None` and stable ids: a card left overnight still works."""

    def __init__(self, task_id: int | None, handle) -> None:
        super().__init__(timeout=None)
        self._handle = handle
        for label, decision, style in (
            ("Approve", "approve", discord.ButtonStyle.success),
            ("Reject", "reject", discord.ButtonStyle.secondary),
        ):
            button = discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"{PREFIX}:{decision}:{task_id}",
            )
            button.callback = self._answered
            self.add_item(button)

    async def _answered(self, interaction) -> None:
        await self._handle(
            interaction.data["custom_id"], by=str(interaction.user)
        )
        await interaction.response.edit_message(
            content=f"{interaction.message.content}\n\n_answered by "
            f"{interaction.user}_",
            view=None,
        )
