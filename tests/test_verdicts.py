"""Ticket 29 — the operator saying a classification was right, or wrong.

The guarantee this module exists to make: **silence is not approval**. A
classification nobody marked is one nobody read, and it never becomes an
example. Every test here is a guard on some edge of that.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import make_event

from friday.verdicts import Mark, mark_for


# --- what a reaction means --------------------------------------------------


@pytest.mark.parametrize("emoji", ["✅", "☑️", "👍"])
def test_the_agreeing_reactions_mean_right(emoji):
    assert mark_for(emoji) is Mark.RIGHT


@pytest.mark.parametrize("emoji", ["❌", "✖️", "👎"])
def test_the_disagreeing_reactions_mean_wrong(emoji):
    assert mark_for(emoji) is Mark.WRONG


@pytest.mark.parametrize("bare,selector", [("☑", "☑️"), ("✖", "✖️")])
def test_the_variation_selector_does_not_decide_whether_a_mark_counts(bare, selector):
    """Discord clients disagree about sending U+FE0F. Comparing raw strings
    means the operator reacts, nothing happens, and there is no error
    anywhere to notice — the worst shape a bug can take."""
    assert mark_for(bare) is mark_for(selector) is not None


def test_a_reaction_we_do_not_recognise_means_nothing():
    """People react for their own reasons. Reading a shrug as a judgement is
    putting words in their mouth."""
    assert mark_for("🎉") is None
    assert mark_for("") is None


# --- recording --------------------------------------------------------------


async def test_a_mark_is_recorded_with_who_and_when(db):
    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="right", by="operator"
    )

    assert await db.verdict_for(provider="fake", provider_message_id="m1") == (
        "right",
        "operator",
    )


async def test_marking_the_same_thing_twice_leaves_one_row(db):
    """Changing their mind replaces. A history of reactions is a history
    nobody reads; what matters is what they currently think."""
    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="right", by="operator"
    )
    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="wrong", by="operator"
    )

    assert await db.verdict_for(provider="fake", provider_message_id="m1") == (
        "wrong",
        "operator",
    )


async def test_taking_the_mark_back_leaves_no_row(db):
    """"Unmarked" and "never marked" mean the same thing downstream: nobody
    is vouching for this one."""
    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="right", by="operator"
    )

    await db.clear_verdict(provider="fake", provider_message_id="m1")

    assert await db.verdict_for(provider="fake", provider_message_id="m1") is None


async def test_clearing_something_never_marked_is_not_an_error(db):
    await db.clear_verdict(provider="fake", provider_message_id="never")

    assert await db.verdict_for(provider="fake", provider_message_id="never") is None


# --- the guarantee ----------------------------------------------------------


async def _classified(db, message_id: str, kind: str, text: str) -> None:
    """A message that triage has looked at and reached a conclusion about."""
    event = make_event(message_id=message_id, text=text)
    await db.record_message(event)
    await db.mark_triaged(
        event, decision={"type": kind, "confidence": 0.9, "params": {}}
    )


async def test_only_what_was_marked_right_becomes_an_example(db):
    """The whole ticket. An unmarked classification is one nobody read, and
    an agent that learns from its own unreviewed output drifts with no floor."""
    await _classified(db, "m1", "api_issue", "checkout is 500ing")
    await _classified(db, "m2", "skip", "anyone want lunch")
    await _classified(db, "m3", "doc_question", "where is the spec")

    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="right", by="operator"
    )
    await db.record_verdict(
        provider="fake", provider_message_id="m2", mark="wrong", by="operator"
    )
    # m3 is never marked.

    examples = await db.confirmed_classifications()

    assert examples == [("checkout is 500ing", "api_issue")]


async def test_a_mark_taken_back_stops_being_an_example(db):
    await _classified(db, "m1", "api_issue", "checkout is 500ing")
    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="right", by="operator"
    )
    assert await db.confirmed_classifications() != []

    await db.clear_verdict(provider="fake", provider_message_id="m1")

    assert await db.confirmed_classifications() == []


async def test_changing_right_to_wrong_removes_it_from_the_examples(db):
    await _classified(db, "m1", "api_issue", "checkout is 500ing")
    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="right", by="operator"
    )

    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="wrong", by="operator"
    )

    assert await db.confirmed_classifications() == []


async def test_the_newest_marks_come_first(db):
    for n, text in ((1, "first"), (2, "second"), (3, "third")):
        await _classified(db, f"m{n}", "api_issue", text)
        await db.record_verdict(
            provider="fake", provider_message_id=f"m{n}", mark="right", by="operator"
        )

    examples = await db.confirmed_classifications(limit=2)

    assert [text for text, _ in examples] == ["third", "second"]


# --- the reaction handler ---------------------------------------------------


class FakeClient:
    def __init__(self, me_id: int = 1):
        self.user = SimpleNamespace(id=me_id)


def _provider():
    from friday.providers.discord.user import DiscordUserProvider

    return DiscordUserProvider("token", client=FakeClient())


def _reaction(emoji: str, message_id: str = "m1"):
    return SimpleNamespace(emoji=emoji, message=SimpleNamespace(id=message_id))


async def test_the_operators_reaction_is_reported():
    seen: list[tuple] = []
    provider = _provider()
    provider.on_verdict = lambda **kw: seen.append(kw)

    await provider._handle_reaction_add(
        _reaction("✅"), SimpleNamespace(id=1, __str__=lambda _: "operator")
    )

    assert seen[0]["provider_message_id"] == "m1"
    assert seen[0]["mark"] is Mark.RIGHT


async def test_removing_a_reaction_reports_no_mark():
    seen: list[tuple] = []
    provider = _provider()
    provider.on_verdict = lambda **kw: seen.append(kw)

    await provider._handle_reaction_remove(
        _reaction("✅"), SimpleNamespace(id=1, __str__=lambda _: "operator")
    )

    assert seen[0]["mark"] is None


async def test_somebody_elses_reaction_is_not_a_judgement():
    """Anyone in the channel reacts for their own reasons. Reading a
    colleague's thumbs-up as a verdict on our classification would be putting
    words in their mouth."""
    seen: list[tuple] = []
    provider = _provider()
    provider.on_verdict = lambda **kw: seen.append(kw)

    await provider._handle_reaction_add(
        _reaction("✅"), SimpleNamespace(id=999, __str__=lambda _: "someone else")
    )

    assert seen == []


async def test_an_unrecognised_reaction_is_ignored():
    seen: list[tuple] = []
    provider = _provider()
    provider.on_verdict = lambda **kw: seen.append(kw)

    await provider._handle_reaction_add(
        _reaction("🎉"), SimpleNamespace(id=1, __str__=lambda _: "operator")
    )

    assert seen == []


async def test_nothing_happens_when_nobody_is_listening():
    """The provider reports; it does not store. With no handler wired there
    is nothing to do and nothing to fail."""
    provider = _provider()

    await provider._handle_reaction_add(
        _reaction("✅"), SimpleNamespace(id=1, __str__=lambda _: "operator")
    )


# --- the examples the classifier is given -----------------------------------


def test_no_examples_means_no_examples_block():
    """A fresh install has nothing marked, and stays that way until somebody
    reacts. The instructions must not grow an empty heading."""
    from friday.triage import INSTRUCTIONS, _examples_block

    assert _examples_block(()) == ""


def test_examples_are_rendered_with_what_they_turned_out_to_be():
    from friday.triage import _examples_block

    block = _examples_block([("checkout is 500ing", "api_issue")])

    assert "checkout is 500ing" in block
    assert "api_issue" in block
