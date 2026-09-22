"""Ticket 16 — the second agent.

It writes in the operator's voice, from examples of how they actually write
rather than from a description of it. Nothing it produces reaches a channel
without approval: a template could go out unreviewed because it was the same
sentence every time, and that stops being true the moment a model writes it.
"""

from __future__ import annotations

from friday.sdk.testing import FunctionModel
from friday.sdk.testing import ScriptedModel, assistant_message

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

    def _down(messages, info):
        raise RuntimeError("provider down")

    draft = await Responder(
        config=CONFIG, model=FunctionModel(_down, model_name="test-model")
    ).draft(asking="ask", context=(), tone=TONE)

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


async def test_a_responder_given_a_store_can_reach_its_own_memory():
    """The responder is the obvious first agent to get these — it writes text
    a person reads, and how a room actually likes to be answered is exactly
    the kind of thing worth writing down. Driven end to end: a scripted model
    that calls `memory_search`, and the tool actually reaching the store
    scoped to the channel this draft is about."""
    from friday.sdk.testing import function_call

    from friday.domain.models import FridayState

    seen = {}

    class Store:
        async def room_summary(self, channel_id):
            return None

        async def memory_search(self, scope, query, kind, limit):
            seen["scope"] = scope
            return []

    responder = Responder(
        config=CONFIG,
        model=ScriptedModel(
            [
                [function_call("memory_search", {"query": "tone"}, call_id="1")],
                [assistant_message("cho anh xin correlationId nhé")],
            ]
        ),
        db=Store(),
    )

    await responder.draft(
        asking="ask", context=(), tone=TONE,
        state=FridayState(channel_id="c1", agent="responder").for_task(42),
    )

    assert seen["scope"] == FridayState(channel_id="c1", task_id=42, agent="responder")


async def test_the_claim_and_the_tools_come_from_one_fact_not_two():
    """Same rule as skills: an agent told about a tool it does not have goes
    looking for a door that is not in the room — and the two halves of that
    have to be checked on the *same* construction, not on `build_input` called
    with a flag by hand, which cannot tell "the flag is right" from "the flag
    and the wiring happen to agree today".

    The claim is checked on the real per-call prompt `draft()` sends — the
    section lives in `build_input`'s output, not in the static
    `instructions` — captured through `record=`, the same sink `collecting()`
    already uses elsewhere in this file. The wiring is checked by reaching
    into `._run.tools`, the way
    `test_max_tokens_reaches_the_model_settings_without_a_knob_for_it`
    already reaches into `.agent.model_settings` — one specific construction,
    not a production caller `harness.py`'s own rule is about.
    """
    from friday.domain.models import FridayState

    class Store:
        async def room_summary(self, channel_id):
            return None

        async def memory_search(self, scope, query, kind, limit):
            return []

    without_calls, without_sink = collecting()
    without = Responder(
        config=CONFIG, model=ScriptedModel([[assistant_message("ok")]]),
        record=without_sink,
    )
    await without.draft(asking="ask", context=(), tone=TONE)

    with_calls, with_sink = collecting()
    with_store = Responder(
        config=CONFIG, model=ScriptedModel([[assistant_message("ok")]]),
        db=Store(), record=with_sink,
    )
    await with_store.draft(
        asking="ask", context=(), tone=TONE,
        state=FridayState(channel_id="c1", agent="responder"),
    )

    without_names = {t.name for t in without._run.tools}
    with_names = {t.name for t in with_store._run.tools}

    assert "memory_search" not in without_names
    assert "memory_search" not in without_calls[0].prompt

    assert "memory_search" in with_names
    assert "memory_search" in with_calls[0].prompt


def test_a_responder_declares_the_run_state_whether_or_not_it_has_memory(monkeypatch):
    """One slot, one type, every time (board `every-answer-has-a-shape`,
    ticket 09).

    **This reverses what this test used to assert**, and the reversal is the
    point rather than a relaxation. It read: "a responder given no store should
    not claim a context type its tools do not need" — true while the run
    context meant one thing, where the memory tools read their scope. It means
    the run's whole state now: which room, which task, which message, which
    agent, read by the recording sink as well (D8). A responder with no memory
    tools still has all of those, so a context type that appeared only when the
    tools did was the last place the slot's meaning depended on how the agent
    was built.

    The SDK's context typing is cosmetic at runtime — passing `context=`
    populates a tool's `ctx.context` regardless of what `Harness` was
    constructed with, confirmed separately. What this pins is structural: a
    reader asking what a run carries finds the answer on the construction.
    """
    import friday.responder as responder_module

    given: list = []

    class Spy(responder_module.Harness):
        def __init__(self, **kw):
            given.append(kw.get("context_type"))
            super().__init__(**kw)

    monkeypatch.setattr(responder_module, "Harness", Spy)

    class Store:
        async def room_summary(self, channel_id):
            return None

        async def memory_search(self, scope, query, kind, limit):
            return []

    from friday.domain.models import FridayState

    Responder(config=CONFIG, model=ScriptedModel([]), db=Store())
    Responder(config=CONFIG, model=ScriptedModel([]))

    assert given == [FridayState, FridayState]


def test_the_skill_catalogue_is_in_the_instructions_not_the_per_call_input():
    """It was in `build_input`, so the operator reading a responder's
    *instruction* prompt found no mention of skills at all — while the agent
    called `fetch_skill` anyway, off the tool schema alone.

    Two things are wrong with that, and the second is the one that costs.
    A prompt that describes no door the model is standing in front of is the
    failure this repo already names. And the catalogue is **stable**:
    `SkillLibrary.load()` globs once at startup and its own docstring says a
    skill added while the process runs appears at the next restart. Stable
    bytes belong at the front, where a provider's cache reuses them — the
    same argument that puts triage's few-shot examples in instructions rather
    than sending them again on every classification.
    """
    from friday.agent.instruction_prompt import SkillMeta
    from friday.responder.prompt import build_input, build_instructions

    meta = [
        SkillMeta(
            name="trace-a-request",
            description="follow one request through the logs",
            mutability="custom",
            location="/skills/trace/SKILL.md",
            allowed_tools=(),
        ),
    ]

    told = build_instructions(skills_meta=meta)
    per_call = build_input(asking="which environment?")

    assert "<name>trace-a-request</name>" in told, (
        "the catalogue is not in the instructions"
    )
    assert "trace-a-request" not in per_call, (
        "the catalogue is still being re-sent on every call"
    )


def test_a_responder_with_no_skills_says_nothing_about_them():
    """`available=False` renders nothing, which is the rule: an agent told
    about a tool it does not have goes looking for it."""
    from friday.responder.prompt import build_instructions

    assert "fetch_skill" not in build_instructions()


async def test_a_memory_is_attributed_to_the_responder_whoever_handed_the_state_over():
    """Provenance is "who wrote this, and while doing what", and `draft` is
    where the answer is known for certain.

    It used to be a guarantee by construction — the scope was built inside this
    method with `agent="responder"` written into it. Board
    `every-answer-has-a-shape` moved the state in from the pool, and taking the
    caller's word for the agent would mean a memory written during a draft
    could be attributed to whoever ran before: the state travels a whole
    message's journey, and triage is at the front of it.
    """
    from friday.sdk.testing import function_call

    from friday.domain.models import FridayState

    seen = {}

    class Store:
        async def room_summary(self, channel_id):
            return None

        async def memory_search(self, scope, query, kind, limit):
            seen["scope"] = scope
            return []

    responder = Responder(
        config=CONFIG,
        model=ScriptedModel([
            [function_call("memory_search", {"query": "tone"}, call_id="1")],
            [assistant_message("cho anh xin correlationId nhé")],
        ]),
        db=Store(),
    )

    await responder.draft(
        asking="ask", context=(), tone=TONE,
        state=FridayState(channel_id="c1", agent="triage").for_task(42),
    )

    assert seen["scope"].agent == "responder"
    assert seen["scope"].task_id == 42, "the rest of the state came through"
