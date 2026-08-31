"""Ticket 06 — asking, in Discord.

Buttons are an application-only feature: a user account cannot send message
components. So approval flows through the bot, which also gives a clean record
of who clicked and when. The bot sends nothing else.

Verified live before any of this was written: the bot DMs the operator while
sharing no guild with them, on `Intents.none()`.
"""

from __future__ import annotations

from types import SimpleNamespace

from friday.conversation import ConversationId
from friday.models import Outbound
from friday.outbox import Kind
from friday.providers.discord.bot import DiscordBot

OPERATOR = 482447107983147039


def card(task_id: int = 7, text: str = "cho anh xin cái correlationId") -> Outbound:
    return Outbound(
        id=1, task_id=task_id, conversation=ConversationId("discord", "999"),
        kind=Kind.APPROVAL_CARD, sender="discord_bot", text=text,
    )


class Recipient:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})
        return SimpleNamespace(id=555)


def stub_client(recipient):
    async def fetch_user(_id):
        return SimpleNamespace(dm_channel=recipient, create_dm=lambda: recipient)

    return SimpleNamespace(fetch_user=fetch_user, add_view=lambda _v: None)


async def test_the_card_goes_to_the_operator_as_a_direct_message():
    """Not into the channel: the proposed reply is not public until approved."""
    recipient = Recipient()
    bot = DiscordBot("token", operator_id=OPERATOR, client=stub_client(recipient))

    sent = await bot.send(card())

    assert sent == "555"
    assert len(recipient.sent) == 1


async def test_the_card_shows_the_reply_and_where_it_would_go():
    recipient = Recipient()
    bot = DiscordBot("token", operator_id=OPERATOR, client=stub_client(recipient))

    await bot.send(card(text="cho anh xin cái correlationId"))

    rendered = str(recipient.sent[0])
    assert "cho anh xin cái correlationId" in rendered
    assert "999" in rendered


async def test_the_buttons_carry_the_task_so_a_restart_does_not_lose_them():
    """A decision may be made hours later, after a redeploy. The task id lives
    in the button rather than in this process's memory."""
    recipient = Recipient()
    bot = DiscordBot("token", operator_id=OPERATOR, client=stub_client(recipient))

    await bot.send(card(task_id=42))

    ids = [c.custom_id for c in recipient.sent[0]["view"].children]
    assert ids == ["friday:approve:42", "friday:reject:42"]


async def test_approving_reports_the_task_and_who_decided():
    decisions: list = []
    bot = DiscordBot(
        "token", operator_id=OPERATOR, client=stub_client(Recipient()),
        on_decision=lambda **kw: decisions.append(kw),
    )

    await bot.handle("friday:approve:42", by="longle_")

    assert decisions == [{"task_id": 42, "approved": True, "by": "longle_"}]


async def test_rejecting_reports_it_too():
    decisions: list = []
    bot = DiscordBot(
        "token", operator_id=OPERATOR, client=stub_client(Recipient()),
        on_decision=lambda **kw: decisions.append(kw),
    )

    await bot.handle("friday:reject:42", by="longle_")

    assert decisions == [{"task_id": 42, "approved": False, "by": "longle_"}]


async def test_a_button_that_is_not_ours_is_ignored():
    decisions: list = []
    bot = DiscordBot(
        "token", operator_id=OPERATOR, client=stub_client(Recipient()),
        on_decision=lambda **kw: decisions.append(kw),
    )

    await bot.handle("something:else:1", by="longle_")

    assert decisions == []


def stuck(task_id: int = 7, text: str = "api_issue #7 — correlation_id: abc-123"):
    return Outbound(
        id=2, task_id=task_id, conversation=ConversationId("discord", "999"),
        kind=Kind.HELP_WANTED, sender="discord_bot", text=text,
    )


async def test_being_stuck_is_told_not_asked():
    """There is no decision to make. Buttons on it would be a question the
    operator has to work out the meaning of."""
    recipient = Recipient()
    bot = DiscordBot("token", operator_id=OPERATOR, client=stub_client(recipient))

    await bot.send(stuck())

    (message,) = recipient.sent
    assert message.get("view") is None
    assert "abc-123" in message["content"]


async def test_it_says_where_to_look():
    recipient = Recipient()
    bot = DiscordBot(
        "token", operator_id=OPERATOR, client=stub_client(recipient),
        board_url="http://localhost:8086",
    )

    await bot.send(stuck())

    assert "localhost:8086" in recipient.sent[0]["content"]
