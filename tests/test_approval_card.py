"""The approval card tells the whole truth (DESIGN-v2 §12, ticket 17).

A reply goes out in the operator's name, into a channel other people read. The
card is the last place a lie in the draft can be caught, so it shows exactly
what would be sent, where and to whom, what any link or mention resolves to, and
whether a secret was found and redacted. Built in the kernel so it reads the
same however it is delivered — the Discord adapter only transports it.
"""

from __future__ import annotations

from friday.kernel.domain.conversation import ConversationId
from friday.kernel import outbox_card as card
from friday.sdk.redact import clear_secret_values, register_secret_values

WHERE = ConversationId("discord", "999")


def render(text: str, reply_id: int = 12) -> str:
    return card.render(text, destination=WHERE, reply_id=reply_id)


def test_it_shows_the_bytes_the_destination_and_the_audience():
    body = render("the checkout api is back up")

    assert "the checkout api is back up" in body
    assert str(WHERE) in body           # discord:999
    assert "public" in body             # a reply posts into the conversation
    assert "goes out as you" in body
    assert "reply 12" in body


def test_it_names_the_row_it_asks_about():
    assert "reply 7" in render("ok", reply_id=7)


def test_a_markdown_link_reveals_where_it_actually_points():
    """`[our docs](evil)` reads as trustworthy and points anywhere. The card
    shows the real destination beside the label."""
    body = render("see [our docs](https://evil.example.invalid/steal)")

    assert "https://evil.example.invalid/steal" in body
    assert "our docs" in body


def test_a_bare_link_is_surfaced():
    assert "https://example.invalid/x" in render("go to https://example.invalid/x")


def test_a_hidden_mention_is_surfaced():
    """A `<@id>` shows as a name in the client but is a silent ping in the
    bytes; `@everyone` notifies the whole channel."""
    body = render("thanks <@123> — <@&456> please review")

    assert "123" in body and "pings user" in body
    assert "456" in body and "pings role" in body


def test_broadcast_is_flagged():
    assert "@everyone" in render("heads up @everyone")


def test_a_secret_by_pattern_is_redacted_and_flagged():
    """The shown bytes are what will go out — scrubbed — and the card says a
    redaction happened, so the operator is not approving a bare [REDACTED]."""
    body = render("key is sk-abcdefghijklmnopqrstuvwxyz01 ok")

    assert "sk-abcdefghijklmnopqrstuvwxyz01" not in body
    assert "[REDACTED]" in body
    assert "shaped like a credential" in body


def test_a_declared_secret_by_value_is_redacted_and_flagged():
    try:
        register_secret_values(["totally-unshaped-secret-9"])
        body = render("token totally-unshaped-secret-9 here")
    finally:
        clear_secret_values()

    assert "totally-unshaped-secret-9" not in body
    assert "[REDACTED]" in body
    assert "declared secret" in body


def test_a_clean_draft_has_no_flags():
    body = render("cho anh xin cái correlationId")

    assert "[REDACTED]" not in body
    assert "⚠" not in body
