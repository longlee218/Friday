"""Ticket 06 — asking, in Discord.

Buttons are an application-only feature: a user account cannot send message
components. So approval flows through the bot, which also gives a clean record
of who clicked and when. The bot sends nothing else.

Verified live before any of this was written: the bot DMs the operator while
sharing no guild with them, on `Intents.none()`.
"""

from __future__ import annotations

from types import SimpleNamespace

from friday.kernel.domain.conversation import ConversationId
from friday.kernel.domain.outbound import Outbound
from friday.kernel.outbox import Kind
from friday.kernel.providers.discord.bot import DiscordBot

OPERATOR = 482447107983147039


def card(
    task_id: int = 7, text: str = "cho anh xin cái correlationId", approves: int = 12
) -> Outbound:
    return Outbound(
        id=1,
        task_id=task_id,
        conversation=ConversationId("discord", "999"),
        kind=Kind.APPROVAL_CARD,
        sender="discord_bot",
        text=text,
        approves=approves,
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


async def test_the_card_is_transported_verbatim():
    """The card body is built in the kernel (`outbox.card`) — the exact bytes,
    the destination, the audience, the secret flags — and carried in `row.text`.
    This adapter shows it unchanged and adds only the buttons a user account
    cannot send; it does not compose or edit the truth about a draft."""
    from friday.kernel import outbox_card as card_renderer
    from friday.kernel.domain.conversation import ConversationId

    body = card_renderer.render(
        "cho anh xin cái correlationId",
        destination=ConversationId("discord", "999"),
        reply_id=12,
    )
    recipient = Recipient()
    bot = DiscordBot("token", operator_id=OPERATOR, client=stub_client(recipient))

    await bot.send(card(text=body))

    assert recipient.sent[0]["content"] == body
    assert "999" in body and "reply 12" in body


async def test_the_buttons_carry_the_row_they_approve():
    """Approval belongs to the reply row, not the task (board
    read-it-the-way-the-operator-does, ticket 12): a task can queue two
    replies, and approving the first must not approve the second. The row id
    lives in the button rather than in this process's memory."""
    recipient = Recipient()
    bot = DiscordBot("token", operator_id=OPERATOR, client=stub_client(recipient))

    await bot.send(card(task_id=42, approves=12))

    ids = [c.custom_id for c in recipient.sent[0]["view"].children]
    assert ids == ["friday:approve:row:12", "friday:reject:row:12"]


async def test_approving_reports_the_row_and_who_decided():
    decisions: list = []
    bot = DiscordBot(
        "token",
        operator_id=OPERATOR,
        client=stub_client(Recipient()),
        on_decision=lambda **kw: decisions.append(kw),
    )

    await bot.handle("friday:approve:row:12", by="longle_", by_id=OPERATOR)

    assert decisions == [
        {"outbound_id": 12, "approved": True, "by": "longle_", "by_id": OPERATOR}
    ]


async def test_rejecting_reports_it_too():
    decisions: list = []
    bot = DiscordBot(
        "token",
        operator_id=OPERATOR,
        client=stub_client(Recipient()),
        on_decision=lambda **kw: decisions.append(kw),
    )

    await bot.handle("friday:reject:row:12", by="longle_", by_id=OPERATOR)

    assert decisions == [
        {"outbound_id": 12, "approved": False, "by": "longle_", "by_id": OPERATOR}
    ]


async def test_a_card_from_before_the_move_is_not_read_as_a_row():
    """A card sent before approval moved to the row carries a *task* id.
    Read as a row id it would approve whatever reply happens to have that
    number, so it is ignored instead."""
    decisions: list = []
    bot = DiscordBot(
        "token",
        operator_id=OPERATOR,
        client=stub_client(Recipient()),
        on_decision=lambda **kw: decisions.append(kw),
    )

    await bot.handle("friday:approve:42", by="longle_", by_id=OPERATOR)

    assert decisions == []


async def test_a_button_that_is_not_ours_is_ignored():
    decisions: list = []
    bot = DiscordBot(
        "token",
        operator_id=OPERATOR,
        client=stub_client(Recipient()),
        on_decision=lambda **kw: decisions.append(kw),
    )

    await bot.handle("something:else:1", by="longle_", by_id=OPERATOR)

    assert decisions == []


def stuck(task_id: int = 7, text: str = "trace_problem #7 — correlation_id: abc-123"):
    return Outbound(
        id=2,
        task_id=task_id,
        conversation=ConversationId("discord", "999"),
        kind=Kind.HELP_WANTED,
        sender="discord_bot",
        text=text,
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
        "token",
        operator_id=OPERATOR,
        client=stub_client(recipient),
        board_url="http://localhost:8086",
    )

    await bot.send(stuck())

    assert "localhost:8086" in recipient.sent[0]["content"]


async def test_telling_the_operator_about_a_row_that_belongs_to_no_task(caplog):
    """A liveness alert and the daily summary belong to no task — there is a
    migration named for that. The log line rendered `row.task_id` with `%d`,
    which raises inside logging on None: the message was delivered, and a
    traceback was printed anyway. A traceback nobody can act on is how the
    real ones stop being read."""
    import logging

    recipient = Recipient()
    bot = DiscordBot("token", operator_id=OPERATOR, client=stub_client(recipient))
    alert = Outbound(
        id=4,
        task_id=None,
        conversation=ConversationId("discord", "999"),
        kind=Kind.ALERT,
        sender="discord_bot",
        text="Alive. 27 messages held.",
    )

    with caplog.at_level(logging.INFO, logger="friday.kernel.providers.discord.bot"):
        assert await bot.send(alert) == "555"

    (line,) = [r.getMessage() for r in caplog.records]
    assert "None" not in line
    assert str(Kind.ALERT) in line


class _User:
    """A Discord user as `_answered` reads it: prints as a name, carries an id."""

    def __init__(self, name: str, id: int) -> None:
        self._name = name
        self.id = id

    def __str__(self) -> str:
        return self._name


class Pressed:
    """What discord.py hands a button callback, reduced to what `_answered`
    reads and writes."""

    def __init__(self, custom_id: str, content: str = "**Reply to 999?**"):
        self.data = {"custom_id": custom_id}
        # A Discord user prints as its name and carries the authenticated id
        # the kernel checks against operator_id.
        self.user = _User("longle_", OPERATOR)
        self.message = SimpleNamespace(content=content)
        self.edits: list[dict] = []

        async def edit_message(**kwargs):
            self.edits.append(kwargs)

        self.response = SimpleNamespace(edit_message=edit_message)


async def press(bot: DiscordBot, custom_id: str) -> Pressed:
    from friday.kernel.providers.discord.bot import _Buttons

    interaction = Pressed(custom_id)
    await _Buttons(12, bot.handle).children[0].callback(interaction)
    return interaction


async def test_a_recorded_decision_says_who_answered():
    bot = DiscordBot(
        "token",
        operator_id=OPERATOR,
        client=stub_client(Recipient()),
        on_decision=lambda **kw: None,
    )

    pressed = await press(bot, "friday:approve:row:12")

    (edit,) = pressed.edits
    assert "answered by longle_" in edit["content"]


async def test_a_press_carries_the_authenticated_pressers_id():
    """The decision must carry who the channel authenticated, not the button's
    payload, so the kernel can check it against operator_id."""
    decisions: list = []
    bot = DiscordBot(
        "token",
        operator_id=OPERATOR,
        client=stub_client(Recipient()),
        on_decision=lambda **kw: decisions.append(kw),
    )

    await press(bot, "friday:approve:row:12")

    assert decisions[0]["by_id"] == OPERATOR and decisions[0]["by"] == "longle_"


async def test_a_card_from_before_the_move_says_nothing_was_recorded(caplog):
    """Review of ticket 12: the stale card was edited to "answered by", so
    the operator saw a decision recorded while nothing was approved and a
    rejection handed nothing over. The card has to say the press did nothing,
    and what to do instead; the log has to say it too."""
    import logging

    bot = DiscordBot(
        "token",
        operator_id=OPERATOR,
        client=stub_client(Recipient()),
        on_decision=lambda **kw: None,
    )

    with caplog.at_level(logging.WARNING, logger="friday.kernel.providers.discord.bot"):
        pressed = await press(bot, "friday:approve:42")

    (edit,) = pressed.edits
    assert "answered by" not in edit["content"]
    assert "nothing was recorded" in edit["content"]
    assert any("friday:approve:42" in r.getMessage() for r in caplog.records)
