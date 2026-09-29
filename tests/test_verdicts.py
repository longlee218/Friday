"""Ticket 29 — the operator saying a classification was right, or wrong.

The guarantee this module exists to make: **silence is not approval**. A
classification nobody marked is one nobody read, and it never becomes an
example. Every test here is a guard on some edge of that.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import make_event

from friday.kernel.memory.verdicts import Mark, mark_for


# --- what a reaction means --------------------------------------------------


@pytest.mark.parametrize("emoji", ["✅", "☑️"])
def test_the_agreeing_reactions_mean_right(emoji):
    assert mark_for(emoji) is Mark.RIGHT


@pytest.mark.parametrize("emoji", ["❌", "✖️"])
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


@pytest.mark.parametrize("everyday", ["👍", "👎"])
def test_the_everyday_reactions_are_deliberately_not_marks(everyday):
    """A thumbs-up is the most ordinary reaction on Discord — "ok anh nhé" to
    a colleague. If it counted, every one of them landing on a classified
    message would quietly become a training example, which is the exact thing
    this ticket's guarantee exists to prevent."""
    assert mark_for(everyday) is None


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
    await _classified(db, "m1", "backend.trace_problem", "checkout is 500ing")
    await _classified(db, "m2", "skip", "anyone want lunch")
    await _classified(db, "m3", "backend.answer_question", "where is the spec")

    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="right", by="operator"
    )
    await db.record_verdict(
        provider="fake", provider_message_id="m2", mark="wrong", by="operator"
    )
    # m3 is never marked.

    examples = await db.confirmed_classifications()

    assert examples == [("checkout is 500ing", "backend.trace_problem")]


async def test_a_mark_taken_back_stops_being_an_example(db):
    await _classified(db, "m1", "backend.trace_problem", "checkout is 500ing")
    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="right", by="operator"
    )
    assert await db.confirmed_classifications() != []

    await db.clear_verdict(provider="fake", provider_message_id="m1")

    assert await db.confirmed_classifications() == []


async def test_changing_right_to_wrong_removes_it_from_the_examples(db):
    await _classified(db, "m1", "backend.trace_problem", "checkout is 500ing")
    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="right", by="operator"
    )

    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="wrong", by="operator"
    )

    assert await db.confirmed_classifications() == []


async def test_the_newest_marks_come_first(db):
    for n, text in ((1, "first"), (2, "second"), (3, "third")):
        await _classified(db, f"m{n}", "backend.trace_problem", text)
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
    from friday.kernel.providers.discord.user import DiscordUserProvider

    return DiscordUserProvider("token", client=FakeClient())


def _payload(emoji: str, message_id: str = "m1", user_id: int = 1):
    """The raw gateway payload. Raw rather than the rich event because the
    rich one only fires for messages still in the library's memory cache,
    which is empty at every restart."""
    return SimpleNamespace(
        emoji=emoji, message_id=message_id, user_id=user_id, channel_id="watched"
    )


async def test_the_operators_reaction_is_reported():
    seen: list[dict] = []
    provider = _provider()
    provider.on_verdict = lambda **kw: seen.append(kw)

    await provider._handle_raw_reaction_add(_payload("✅"))

    assert seen[0]["provider_message_id"] == "m1"
    assert seen[0]["mark"] is Mark.RIGHT
    assert seen[0]["taking_back"] is False


async def test_removing_a_reaction_reports_which_one_went():
    """Not just "something was unmarked". Discord leaves an old reaction in
    place when a new one is added, so ✅ then ❌ then remove-the-✅ is the
    natural order — and the caller has to know it was the ✅ that went, or it
    throws away the ❌ still sitting on the message."""
    seen: list[dict] = []
    provider = _provider()
    provider.on_verdict = lambda **kw: seen.append(kw)

    await provider._handle_raw_reaction_remove(_payload("✅"))

    assert seen[0]["mark"] is Mark.RIGHT
    assert seen[0]["taking_back"] is True


async def test_the_raw_event_is_what_is_listened_for():
    """The rich `reaction_add` only fires when the message is still in the
    library's in-memory cache — a deque that starts empty at every restart and
    is filled only by live traffic. Marking anything older, or anything that
    arrived through the recovery sweep, would silently do nothing."""
    provider = _provider()

    assert provider._client.on_raw_reaction_add is not None
    assert not hasattr(provider._client, "on_reaction_add")


async def test_somebody_elses_reaction_is_not_a_judgement():
    """Anyone in the channel reacts for their own reasons. Reading a
    colleague's thumbs-up as a verdict on our classification would be putting
    words in their mouth."""
    seen: list[dict] = []
    provider = _provider()
    provider.on_verdict = lambda **kw: seen.append(kw)

    await provider._handle_raw_reaction_add(_payload("✅", user_id=999))

    assert seen == []


async def test_an_unrecognised_reaction_is_ignored():
    seen: list[dict] = []
    provider = _provider()
    provider.on_verdict = lambda **kw: seen.append(kw)

    await provider._handle_raw_reaction_add(_payload("🎉"))

    assert seen == []


async def test_nothing_happens_when_nobody_is_listening():
    """The provider reports; it does not store. With no handler wired there
    is nothing to do and nothing to fail."""
    provider = _provider()

    await provider._handle_raw_reaction_add(_payload("✅"))


# --- the examples the classifier is given -----------------------------------


def test_no_marks_means_only_the_declared_examples():
    """A fresh install has nothing marked, and stays that way until somebody
    reacts. What the classifier is shown then is the declared examples —
    each action's and the core's `skip` ones — and nothing else: examples
    add up (board `domains-plug-in`, ticket 02 §4), and the operator's marks
    are the part that grows."""
    from friday.kernel.triage.prompt import SKIP_EXAMPLES, build_instructions

    examples = build_instructions().split("<examples>")[1].split("</examples>")[0]
    shown = [line for line in examples.splitlines() if line.startswith("- ")]
    assert len(shown) == len(SKIP_EXAMPLES)


def test_examples_are_rendered_with_what_they_turned_out_to_be():
    """Asserted on the assembled prompt rather than on a private helper: the
    helper was a wrapper that only delegated to `few_shot`, and a test against
    it proved the wrapper worked rather than that the examples reach the
    model."""
    from friday.kernel.triage.prompt import build_instructions

    built = build_instructions(examples=[("checkout is 500ing", "backend.trace_problem")])

    assert "<examples>" in built
    assert "checkout is 500ing" in built
    assert "backend.trace_problem" in built


# --- the marks that must not cancel each other ------------------------------


async def test_removing_an_old_reaction_does_not_delete_the_newer_mark(db):
    """The order that actually happens: ✅, change of mind, add ❌, then tidy
    up by removing the ✅. Discord does not remove the old one for you. A
    clear-on-any-removal would throw away the ❌ that is still on the message
    and leave the classification unmarked while it visibly is not."""
    from friday.kernel.memory.verdicts import Mark

    async def marked(*, provider_message_id, mark, by, taking_back):
        current = await db.verdict_for(
            provider="fake", provider_message_id=provider_message_id
        )
        if taking_back:
            if current is not None and current[0] == str(mark):
                await db.clear_verdict(
                    provider="fake", provider_message_id=provider_message_id
                )
            return
        await db.record_verdict(
            provider="fake",
            provider_message_id=provider_message_id,
            mark=str(mark),
            by=by,
        )

    await marked(
        provider_message_id="m1", mark=Mark.RIGHT, by="op", taking_back=False
    )
    await marked(
        provider_message_id="m1", mark=Mark.WRONG, by="op", taking_back=False
    )
    await marked(
        provider_message_id="m1", mark=Mark.RIGHT, by="op", taking_back=True
    )

    assert await db.verdict_for(provider="fake", provider_message_id="m1") == (
        "wrong",
        "op",
    )


async def test_a_state_recorded_as_a_decision_never_becomes_an_example(db):
    """`mark_triaged` also records the state a message ended in — a
    low-confidence one is stored as `needs_human`. Marking that right is a
    sensible thing for the operator to do ("yes, a person should see this"),
    and showing it back as an example would teach the classifier a label it
    has no tool for."""
    await _classified(db, "m1", "needs_human", "something ambiguous")
    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="right", by="operator"
    )

    assert await db.confirmed_classifications() == []


@pytest.mark.parametrize("kind", ["backend.trace_problem", "ops.request_permission", "backend.answer_question", "skip"])
async def test_every_type_the_classifier_can_produce_can_become_an_example(db, kind):
    """Including `skip`. The hardest thing a classifier learns is when *not*
    to open a task, and a negative example is the only thing that teaches it."""
    await _classified(db, "m1", kind, "some message")
    await db.record_verdict(
        provider="fake", provider_message_id="m1", mark="right", by="operator"
    )

    assert await db.confirmed_classifications() == [("some message", kind)]


async def _confirmed(db, message_id: str, kind: str, text: str) -> None:
    """Classified, and the operator saying that classification was right."""
    await _classified(db, message_id, kind, text)
    await db.record_verdict(
        provider="fake", provider_message_id=message_id, mark="right", by="operator"
    )


async def test_one_afternoon_of_marking_skips_does_not_fill_every_slot(db):
    """Marks arrive in bursts. An afternoon spent confirming that a noisy
    channel is mostly `skip` is a realistic afternoon, and eight of eight
    examples reading "this one is skip" teaches the classifier to skip."""
    await _confirmed(db, "real", "backend.trace_problem", "checkout is 500ing")
    for n in range(12):
        await _confirmed(db, f"lunch{n}", "skip", f"anyone want lunch {n}")

    examples = await db.confirmed_classifications(limit=4)

    kinds = [kind for _, kind in examples]
    assert "backend.trace_problem" in kinds, "the only positive example was crowded out"
    assert len(examples) == 4


async def test_balancing_does_not_invent_types_nobody_confirmed(db):
    """Only what exists is shared out. One type marked means examples of one
    type — this balances, it does not fabricate."""
    for n in range(5):
        await _confirmed(db, f"bug{n}", "backend.trace_problem", f"broken {n}")

    examples = await db.confirmed_classifications(limit=3)

    assert len(examples) == 3
    assert {kind for _, kind in examples} == {"backend.trace_problem"}
