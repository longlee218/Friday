"""Ticket 16 — the second agent.

It writes in the operator's voice, from examples of how they actually write
rather than from a description of it. Nothing it produces reaches a channel
without approval: a template could go out unreviewed because it was the same
sentence every time, and that stops being true the moment a model writes it.
"""

from __future__ import annotations

from agents.models.interface import Model
from agents.testing import ScriptedModel, assistant_message

from conftest import make_event
from friday.config import AgentConfig
from friday.responder import Draft, Responder

CONFIG = AgentConfig(
    name="responder", api_key="k", base_url="https://example.invalid/v1",
    model="test-model", options={}, settings={},
)

TONE = [
    make_event(text="ok để anh xem", is_own=True),
    make_event(text="cho anh xin cái correlationId nhé", is_own=True),
]


def responder_with(*steps, record=None) -> Responder:
    return Responder(config=CONFIG, model=ScriptedModel(list(steps)), record=record)


def collecting() -> tuple[list, object]:
    """A recording sink and the list it fills.

    The sink is handed over when the responder is built, not when it is asked
    for a draft — see D1. A test that wants to read the prompt therefore has
    to say so before the run, which is the same order production works in.
    """
    calls: list = []

    async def sink(call) -> None:
        calls.append(call)

    return calls, sink


async def test_it_writes_a_reply():
    responder = responder_with([assistant_message("cho anh xin cái curl với")])

    draft = await responder.draft(
        asking="Could you send the correlationId?", context=(), tone=TONE
    )

    assert isinstance(draft, Draft)
    assert draft.text == "cho anh xin cái curl với"


async def test_the_operators_own_messages_are_in_the_prompt():
    """Real examples carry a voice that a description of one does not — which
    is the whole reason to read them instead of writing a style guide."""
    calls, sink = collecting()
    responder = responder_with([assistant_message("ok")], record=sink)

    await responder.draft(asking="ask", context=(), tone=TONE)

    (call,) = calls
    assert "cho anh xin cái correlationId nhé" in call.prompt


async def test_the_conversation_is_in_the_prompt():
    calls, sink = collecting()
    responder = responder_with([assistant_message("ok")], record=sink)

    await responder.draft(
        asking="ask",
        context=[make_event(text="api trả 500", author_name="mobile dev")],
        tone=TONE,
    )

    assert "api trả 500" in calls[0].prompt


async def test_a_model_failure_produces_no_draft_rather_than_a_bad_one():
    """The caller falls back to the template. Never a wrong reply in someone
    else's name, and never silence either."""

    class Broken(Model):
        async def get_response(self, *a, **kw):
            raise RuntimeError("provider down")

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    draft = await Responder(config=CONFIG, model=Broken()).draft(
        asking="ask", context=(), tone=TONE
    )

    assert draft is None


async def test_an_empty_answer_produces_no_draft():
    """A model that returns nothing has not written a reply."""
    draft = await responder_with([assistant_message("   ")]).draft(
        asking="ask", context=(), tone=TONE
    )

    assert draft is None


async def test_reasoning_never_reaches_the_reply():
    """MiniMax and other reasoning models put their working in the output. It
    is not a reply, and posting it publishes the model's deliberation about the
    operator's colleagues under the operator's own name."""
    thinking = (
        "<think>\nWhose voice is this? Looking at the examples, they write\n"
        "briefly in Vietnamese. I'll match that.\n</think>\n\n"
        "cho anh xin cái correlationId nhé"
    )

    draft = await responder_with([assistant_message(thinking)]).draft(
        asking="ask", context=(), tone=TONE
    )

    assert draft.text == "cho anh xin cái correlationId nhé"


async def test_an_answer_that_is_only_reasoning_produces_no_draft():
    """Better the template than an empty message."""
    draft = await responder_with([assistant_message("<think>hmm</think>")]).draft(
        asking="ask", context=(), tone=TONE
    )

    assert draft is None


async def test_an_unclosed_reasoning_block_is_not_treated_as_a_reply():
    """A truncated response leaves the tag open. What follows is still working,
    not an answer."""
    draft = await responder_with([assistant_message("<think>reasoning cut off")]).draft(
        asking="ask", context=(), tone=TONE
    )

    assert draft is None


async def test_it_is_told_not_to_address_anyone_by_mention():
    """The outbox already hangs the message under the one it answers, so a
    mention is redundant — and the tone examples are full of them whenever the
    operator has been testing by tagging themselves."""
    from friday.responder import INSTRUCTIONS

    assert "mention" in INSTRUCTIONS.lower()
