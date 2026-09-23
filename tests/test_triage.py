"""Ticket 04 slice 2 — the triage agent.

Driven with the SDK's ScriptedModel: no test makes a network call. What is being
tested is our tool surface and how a tool call becomes a decision — not the
model's judgement, which only a live run can speak to.

**Triage classifies and stops.** Which tool it called is the whole answer; the
only thing it adds is how certain it is. Lifting values out of the message is a
different job with a different failure mode and it belongs to whoever needs
those values — see `friday/extraction.py`. These tests hold that line, because
the tool schema is the only thing stopping a model from being asked to do both.
"""

from __future__ import annotations

import pytest
from friday.sdk.testing import (
    FunctionModel,
    ScriptedModel,
    assistant_message,
    function_call,
)

from conftest import make_event, summary_row
from friday.config import AgentConfig
from friday.triage import Decided, NeedsHuman, Triage
from friday.triage.prefilter import Sensitive

CONFIG = AgentConfig(
    name="triage",
    api_key="k",
    base_url="https://example.invalid/v1",
    model="test-model",
    options={"confidence_threshold": 0.7},
)


def triage_with(*steps) -> Triage:
    return Triage(config=CONFIG, model=ScriptedModel(list(steps)))


def _raising(exc) -> FunctionModel:
    """A model that raises, so a provider failure can be scripted."""

    def fn(messages, info):
        raise exc

    return FunctionModel(fn, model_name="test-model")


def _shown(messages) -> str:
    """Everything the model was shown across a request's message history, as one
    string — instructions and every text part — so a test can assert on what
    actually reached it."""
    bits: list[str] = []
    for message in messages:
        instructions = getattr(message, "instructions", None)
        if instructions:
            bits.append(instructions)
        for part in getattr(message, "parts", []):
            content = getattr(part, "content", None)
            if isinstance(content, str):
                bits.append(content)
            elif isinstance(content, list):
                bits.append(
                    " ".join(
                        item if isinstance(item, str) else getattr(item, "text", "")
                        for item in content
                    )
                )
    return "\n".join(bits)


def _capturing(seen: list) -> FunctionModel:
    """A model that records what it was shown, then answers `api_issue`."""
    from friday.sdk.testing import ModelResponse

    def fn(messages, info):
        seen.append(_shown(messages))
        return ModelResponse(
            parts=[function_call("answer", {"type": "api_issue", "confidence": 0.9})]
        )

    return FunctionModel(fn, model_name="test-model")


async def decide(triage, text="the api is wrong", turn=()):
    return await triage.decide(make_event(text=text), turn=turn)


async def test_an_api_problem_becomes_an_api_issue():
    triage = triage_with([
        function_call("answer", {"type": "api_issue", "confidence": 0.9}, call_id="1")
    ])

    outcome = await decide(triage)

    assert isinstance(outcome, Decided)
    assert outcome.type == "api_issue"
    assert outcome.confidence == 0.9


async def test_a_permission_request_becomes_an_access_request():
    triage = triage_with([
        function_call("answer", {"type": "access_request", "confidence": 0.95}, call_id="1")
    ])

    assert (await decide(triage)).type == "access_request"


async def test_a_question_about_docs_becomes_a_doc_question():
    triage = triage_with([
        function_call("answer", {"type": "doc_question", "confidence": 0.8}, call_id="1")
    ])

    assert (await decide(triage)).type == "doc_question"


async def test_social_talk_becomes_a_skip():
    triage = triage_with([
        function_call("answer", {"type": "skip", "confidence": 0.99}, call_id="1")
    ])

    assert (await decide(triage)).type == "skip"


async def test_a_message_carrying_nothing_still_decides():
    """"the api is wrong" is the most common shape there is and carries no
    values at all. It must still produce a task — that is what triggers asking
    for the fields, and it is why classifying does not depend on extracting."""
    triage = triage_with([
        function_call("answer", {"type": "api_issue", "confidence": 0.6}, call_id="1")
    ])

    assert (await decide(triage)).type == "api_issue"


async def test_answering_without_calling_a_tool_asks_for_a_human():
    """Never-drop: an undecided message becomes a task, not silence."""
    triage = triage_with([assistant_message("I am not sure what this is.")])

    outcome = await decide(triage)

    assert isinstance(outcome, NeedsHuman)


async def test_a_model_failure_asks_for_a_human():
    triage = Triage(config=CONFIG, model=_raising(RuntimeError("provider exploded")))

    outcome = await decide(triage)

    assert isinstance(outcome, NeedsHuman)
    assert "provider exploded" in outcome.reason


async def test_triage_needs_no_database():
    """It reports a decision; applying it belongs to the caller."""
    import inspect

    assert "db" not in inspect.signature(Triage.__init__).parameters


class NeverCalled(FunctionModel):
    """A model that records being reached, and refuses to answer.

    It **counts** rather than only raising, and the difference is the whole
    reason this class is written down. Raising looks like it fails the test —
    it does not: `Harness` turns any failure into `None`, and the caller turns
    that into `NeedsHuman`, which is the *same outcome the prefilter produces*.
    A test asserting only on the outcome cannot tell "held before the call"
    from "sent, and the provider exploded", and the whole point of the
    prefilter is which of those two happened.

    Verified: with `Sensitive.found` stubbed to `return None`, eight tests here
    still passed and this was reached eight times.
    """

    def __init__(self) -> None:
        self.reached = 0

        def fn(messages, info):
            self.reached += 1
            raise AssertionError("the model was called")

        super().__init__(fn, model_name="test-model")


WORDS = Sensitive(["lương", "thưởng", "salary", "bonus", "mật khẩu", "xin api key"])


def guarded() -> tuple[Triage, NeverCalled]:
    """A triage and the model it must not reach. Both are returned, because
    the outcome alone does not say whether it was reached."""
    model = NeverCalled()
    return Triage(config=CONFIG, model=model, sensitive=WORDS), model


async def test_a_sensitive_message_never_reaches_the_model():
    """The harm is in the sending, so the decision is made before the call —
    by a rule a persuasive message cannot argue with.

    Asserted on the *model*, not on the outcome. The outcome is `NeedsHuman`
    either way: that is what the prefilter produces, and it is also what a
    failed call produces."""
    triage, model = guarded()

    outcome = await decide(triage, "lương tháng này về chưa")

    assert model.reached == 0, "the message was sent to the model"
    assert isinstance(outcome, NeedsHuman)


async def test_it_is_held_for_the_operator_and_not_dropped():
    """`NeedsHuman` opens a task in the column the operator watches. This list
    contains words that appear in ordinary reports — "cho em xin api key của
    staging" is an access request — and skipping them would be losing real
    mentions on the strength of one word. The guarantee is that the *model*
    does not see it."""
    triage, model = guarded()

    outcome = await decide(triage, "cho em xin api key của staging")

    assert model.reached == 0
    assert isinstance(outcome, NeedsHuman)
    assert "xin api key" in outcome.reason


def test_the_reason_names_the_word_and_not_the_message():
    """The message is the thing being kept quiet. It must not be copied into
    an explanation that then travels."""
    said = "mật khẩu prod là hunter2"

    assert said not in WORDS.found(said)


@pytest.mark.parametrize(
    "text",
    [
        "lương tháng này về chưa",
        "luong thang nay ve chua",          # no diacritics — how half of it is typed
        "LƯƠNG tháng này?",                 # shouting
        "khi nào có thưởng tết",
        "did the salary come through?",
        "what's the BONUS structure",
        "đổi mật khẩu DB giúp em",
    ],
)
async def test_every_phrasing_of_a_listed_word_is_caught(text):
    triage, model = guarded()

    assert isinstance(await decide(triage, text), NeedsHuman)
    assert model.reached == 0, f"{text!r} was sent to the model"


@pytest.mark.parametrize(
    "text",
    [
        "the payment API returns 500",
        "salary-service is down on staging",   # a system, not a payday
        "can you review the bonuses endpoint",  # `bonuses` is not `bonus`
    ],
)
async def test_a_word_inside_an_identifier_is_not_the_word(text):
    """A hyphen or a suffix makes it a name. Holding every message about
    `salary-service` would make the list unusable in a codebase that has one."""
    triage = triage_with(
        [function_call("answer", {"type": "api_issue", "confidence": 0.9}, call_id="1")],
    )
    triage._sensitive = WORDS

    assert (await decide(triage, text)).type == "api_issue"


async def test_an_empty_list_holds_nothing():
    """An install that has not thought about this yet gets what it would have
    had without the feature, not someone else's guesses about what is sensitive
    in their workplace."""
    triage = triage_with(
        [function_call("answer", {"type": "api_issue", "confidence": 0.9}, call_id="1")],
    )

    assert (await decide(triage, "lương tháng này về chưa")).type == "api_issue"


async def test_a_malformed_classify_call_is_corrected_by_the_model():
    """The model gets one turn to fix its own call, and takes it.

    `classify` asks for a number; a model that sends `"high"` fails the schema
    before the tool body runs, and the SDK hands it back the string saying so.
    That only helps if the run continues — and under
    `tool_use_behavior="stop_on_first_tool"` it did not: the first tool call's
    *output* ends the run, and the SDK cannot tell a failure string from a
    success one, so the correction was the run's final answer and no model
    read it. A mention the model had all but classified became work for a
    person.

    The terminator an agent with a declared shape gets moves it to the thing
    that actually means answered: the tool returning an *instance* of that
    shape, rather than returning anything at all.
    """
    from friday.sdk.testing import ScriptedModel, function_call

    triage = Triage(
        config=CONFIG,
        model=ScriptedModel(
            [
                [function_call("answer", {"type": "api_issue",
                                            "confidence": "high"}, call_id="1")],
                [function_call("answer", {"type": "api_issue",
                                            "confidence": 0.9}, call_id="2")],
            ]
        ),
    )

    outcome = await triage.decide(make_event(text="the api is 500ing"))

    assert outcome == Decided(type="api_issue", confidence=0.9)


async def test_a_model_that_cannot_fix_its_own_call_becomes_a_persons_problem():
    """One correction, not an argument.

    The budget is `max_turns` and nothing else, which is what keeps a model
    that has misunderstood its own schema from spending the highest-volume
    path in the system finding out. Two bad calls exhaust it, the harness
    turns the overrun into a `last_error`, and the mention lands where every
    other triage failure lands. Never dropped, which is the rule this system
    is built on.
    """
    from friday.sdk.testing import ScriptedModel, function_call

    bad = [function_call("answer", {"type": "api_issue",
                                      "confidence": "high"}, call_id="1")]
    triage = Triage(config=CONFIG, model=ScriptedModel([bad, bad, bad]))

    outcome = await triage.decide(make_event(text="the api is 500ing"))

    assert isinstance(outcome, NeedsHuman)
    assert "triage failed" in outcome.reason


# --- triage's prompt is classification and nothing else (ticket 03) ------


def test_triage_carries_no_skill_catalogue_and_cannot_be_given_one():
    """1,008 of triage's 2,272 system-prompt characters described four tools it
    has never once fetched — 44% of the highest-volume prompt in the system —
    and wiring those tools silently tripled its turn budget from 2 to 4.

    **No test pinned this in either direction.** `grep` for skills across
    `tests/test_triage*.py` and for `triage` across `tests/test_skills.py`
    returned nothing: the catalogue arrived on 2026-09-07 and left on ticket 13
    without a single assertion noticing either way.

    Asserted as "cannot be given one" rather than "is not given one", because
    a parameter that still exists is a parameter something can pass. That is
    not hypothetical here: `evals/run_triage_eval.py` never passed `skills=`
    while production always did, so for two days the regression net scored a
    classifier that did not exist. Removing the parameter is what makes the
    eval correct by construction rather than by remembering."""
    import inspect

    from friday.triage import INSTRUCTIONS, Triage
    from friday.triage.runner import build_triage

    # Asserted on what the `Harness` is actually handed, not on the module
    # constant: `INSTRUCTIONS` is `build_instructions()` with no examples, and
    # production builds a *different* string inside `Triage.__init__`. A
    # mutation that appended a catalogue to the real call passed a first
    # version of this test that checked the constant — the same "assert on a
    # proxy" mistake this session has already made twice.
    built = Triage(config=CONFIG)._run.instructions

    for where_from, text in (("the constant", INSTRUCTIONS), ("the agent", built)):
        assert "<skill_system>" not in text, f"a catalogue reached {where_from}"
        assert "answer-in-vietnamese" not in text, f"a skill reached {where_from}"

    for where in (Triage.__init__, build_triage):
        assert "skills" not in inspect.signature(where).parameters, (
            f"{where.__qualname__} can still be handed a skill library"
        )


def test_triage_still_has_exactly_the_one_tool_that_is_its_answer():
    """Removing the catalogue must not remove the answer.

    The answer is the run's *output* now, not a door in the tool list — Pydantic
    AI forces the output tool that carries the closed set (D6 made `classify` and
    `skip` one set on one shape). So triage carries no function tools at all, and
    its answer shape is what remains.
    """
    triage = triage_with()

    assert triage._run.tools == [], "triage carries no doors, only its answer"
    assert triage._run.answers is not None, "the answer shape is still declared"


# --- triage reads a light context (ticket 09) -----------------------------


def test_build_input_carries_the_turn_and_no_room_by_default():
    """No summary, no `<channel_derived>` section at all — a room nobody has
    summarised must not cost the classifier a byte, the same property
    ticket 01 proved for the extractor."""
    from friday.triage.context import LightContext
    from friday.triage.prompt import build_input

    said = build_input(LightContext(turn=[make_event(text="api lỗi")], summary=None))

    assert "<channel_derived>" not in said
    assert "api lỗi" in said


def test_build_input_carries_the_rooms_summary_when_there_is_one():
    """The row ticket 06's summariser writes, read directly — the same
    section the responder reads, not a new one invented for triage."""
    from friday.triage.context import LightContext
    from friday.triage.prompt import build_input

    said = build_input(LightContext(
        turn=[make_event(text="api lỗi")], summary=summary_row(topic="the reelme api"),
    ))

    assert "<channel_derived>" in said
    assert "the reelme api" in said


async def test_build_input_does_not_carry_the_operators_rows(db):
    """Domain rows do not reach triage, the operator's or a model's, here or
    everywhere — it decides a label, not a value, and those rows are exactly
    the kind of thing a value would be built from. `readers_for` gives
    triage the summary and nothing else."""
    from friday.domain.models import FridayState, MemoryKind, MemoryOrigin
    from friday.triage.context import build_light_context
    from friday.triage.prompt import build_input

    for channel in ("watched", "*"):
        await db.memory_add(
            FridayState(channel_id=channel, agent="operator"),
            f"test.apero is staging for {channel}",
            kind=MemoryKind.FACT, origin=MemoryOrigin.ADMIN,
        )
    said = build_input(await build_light_context(
        db, channel_id="watched", turn=[make_event(text="api lỗi")],
    ))

    assert "staging" not in said


def test_build_input_renders_a_real_turn_as_multiple_lines():
    """The point of the whole redesign: a burst of messages is shown as
    itself, not pre-flattened into one string with no clock on any line but
    the first."""
    from friday.triage.context import LightContext
    from friday.triage.prompt import build_input

    turn = [
        make_event(message_id="1", text="api lỗi rồi anh ơi"),
        make_event(message_id="2", text="curl -X GET /pay trả 500"),
    ]
    said = build_input(LightContext(turn=turn, summary=None))

    assert "api lỗi rồi anh ơi" in said
    assert "curl -X GET /pay trả 500" in said
    assert said.count("[") >= 2  # two bracketed timestamps, two lines


async def test_decide_resolves_the_room_from_its_own_store():
    """`Triage` holds what it reads summaries from, and resolves the room per
    call from the event's channel: a store lives as long as the process, a
    room lasts one call."""

    class _Summaries:
        async def room_summary(self, channel_id):
            return (
                summary_row(channel_id, topic="the reelme api")
                if channel_id == "watched"
                else None
            )

    seen = []

    triage = Triage(config=CONFIG, model=_capturing(seen), summaries=_Summaries())

    await triage.decide(make_event(text="api lỗi", channel_id="watched"))
    said = str(seen[0])
    assert "the reelme api" in said

    seen.clear()
    await triage.decide(make_event(text="api lỗi", channel_id="somewhere-else"))
    said = str(seen[0])
    assert "the reelme api" not in said, "a room leaked into another channel"


async def test_decide_renders_the_given_turn_not_just_the_one_event():
    """The `turn=` parameter is what `TriageRunner` now passes instead of the
    unbounded window — the raw messages of the turn, not a pre-joined
    string. Asserted on what the model was actually shown, not merely on the
    outcome: a mutation that dropped `turn` entirely and always rendered
    `[event]` instead still produces a `Decided` outcome from a scripted
    model that does not look at its input, and a first version of this test
    caught nothing because of it."""
    seen = []

    triage = Triage(config=CONFIG, model=_capturing(seen))

    await triage.decide(
        make_event(message_id="1", text="ignored — turn is given instead"),
        turn=[
            make_event(message_id="1", text="api lỗi rồi"),
            make_event(message_id="2", text="curl -X GET /pay trả 500"),
        ],
    )

    said = seen[0]
    assert "ignored — turn is given instead" not in said
    assert "api lỗi rồi" in said
    assert "curl -X GET /pay trả 500" in said


# --- ticket 14: the light context is gathered in one place ----------------


def test_light_context_holds_exactly_the_turn_and_the_summary():
    from friday.triage.context import LightContext

    turn = [make_event(text="api lỗi")]
    row = summary_row()

    context = LightContext(turn=turn, summary=row)

    assert context.turn == turn
    assert context.summary is row


async def test_build_light_context_is_the_only_place_that_resolves_a_room():
    from friday.triage.context import build_light_context

    class _Summaries:
        async def room_summary(self, channel_id):
            return (
                summary_row(channel_id, topic="the reelme api")
                if channel_id == "watched"
                else None
            )

    turn = [make_event(text="api lỗi")]

    context = await build_light_context(_Summaries(), channel_id="watched", turn=turn)
    assert context.turn == turn
    assert context.summary.data["topic"] == "the reelme api"

    elsewhere = await build_light_context(
        _Summaries(), channel_id="somewhere-else", turn=turn
    )
    assert elsewhere.summary is None, "a room leaked into another channel"


async def test_build_light_context_with_no_store_carries_no_room():
    """A bare test with nothing to resolve from — the same behaviour a room
    nobody has summarised already gets."""
    from friday.triage.context import build_light_context

    turn = [make_event(text="api lỗi")]

    context = await build_light_context(None, channel_id="watched", turn=turn)

    assert context.turn == turn
    assert context.summary is None


def test_the_prompt_is_byte_identical_gathered_or_assembled_by_hand():
    """The refactor's own promise: packaging `turn` and `room` into one value
    changes nothing about what a classifier is shown. Built the old way —
    the exact two section calls `build_input` always made — and the new way,
    and compared for equality, not "contains the same words"."""
    from friday.agent.instruction_prompt import assemble, channel_derived, conversation
    from friday.triage.context import LightContext
    from friday.triage.prompt import build_input

    turn = [
        make_event(message_id="1", text="api lỗi rồi anh ơi"),
        make_event(message_id="2", text="curl -X GET /pay trả 500"),
    ]
    room = summary_row(topic="the reelme wrapper api")

    the_old_way = assemble(channel_derived(room), conversation(list(turn), quoted=True))
    the_new_way = build_input(LightContext(turn=turn, summary=room))

    assert the_new_way == the_old_way


async def test_decide_gathers_context_through_the_one_builder(monkeypatch):
    """`Triage.decide` no longer resolves a room itself — it calls
    `build_light_context` exactly once and renders from what it returns.
    Reverting to an inline lookup would leave this spy unreached."""
    import friday.triage as triage_module

    calls = []
    real = triage_module.build_light_context

    async def spy(summaries, *, channel_id, turn):
        calls.append((channel_id, tuple(turn)))
        return await real(summaries, channel_id=channel_id, turn=turn)

    monkeypatch.setattr(triage_module, "build_light_context", spy)

    triage = triage_with([
        function_call("answer", {"type": "api_issue", "confidence": 0.9}, call_id="1")
    ])
    await decide(triage)

    assert len(calls) == 1


async def test_build_light_context_logs_counts_and_sizes_never_content(caplog):
    """One line per build, at debug level — how many messages, how many
    characters, whether a room summary was present. Never a message body or
    a summary field: that would be a second, unredacted renderer of what
    `model_calls` already records."""
    import logging

    from friday.triage.context import build_light_context

    class _Summaries:
        async def room_summary(self, channel_id):
            return summary_row(channel_id, topic="a secret internal hostname")

    turn = [make_event(text="the reporter's own secret words")]

    with caplog.at_level(logging.DEBUG, logger="friday.triage.context"):
        await build_light_context(_Summaries(), channel_id="watched", turn=turn)

    (record,) = caplog.records
    assert "the reporter's own secret words" not in record.message
    assert "a secret internal hostname" not in record.message
    assert "1" in record.message  # one message in the turn
    assert "present" in record.message


# --- prefix stability, one layer up (ticket 09 reverses ticket 26) --------


async def test_the_summary_section_does_not_care_how_much_the_room_has_said(db):
    """Ticket 26's guarantee was `relevant_messages`'s: unbounded, so a
    relevant message once included was never evicted and the prefix a model
    saw on call N was still there on call N+1. That guarantee lived at the
    database layer — every new message widened the window a little.

    This one lives at the summary row, and does not merely assert
    `build_input` is a pure function of a fixed object: it records real
    messages into the same room, through the real store, with **no
    `rebuild_all()` in between** — the heartbeat's own job, which this test
    deliberately does not call — and checks that what `build_light_context`
    hands `build_input` has not moved. `channel_derived` only ever renders the
    summary row, and nothing writes one but the summariser, on its own
    schedule, strictly less often than every message.
    """
    from friday.domain.models import FridayState, MemoryKind
    from friday.triage.context import build_light_context
    from friday.triage.prompt import build_input

    await db.memory_add(
        FridayState(channel_id="watched", agent="summary"), "the reelme wrapper api",
        kind=MemoryKind.SUMMARY,
        data={"topic": "the reelme wrapper api", "facts": ["x"], "summary_of": "1"},
    )

    first = build_input(await build_light_context(
        db, channel_id="watched", turn=[make_event(message_id="1", text="a")],
    ))
    # Real messages, recorded into the actual database — not rebuilt from.
    for n in range(2, 12):
        await db.record_message(make_event(message_id=str(n), text=f"noise {n}"))
    later = build_input(await build_light_context(
        db, channel_id="watched",
        turn=[make_event(message_id="11", text="a later mention")],
    ))

    def summary_section(said: str) -> str:
        return said.split("<conversation>")[0]

    assert "the reelme wrapper api" in summary_section(first)
    assert summary_section(first) == summary_section(later)


def test_two_turns_against_the_same_room_share_everything_but_the_turn():
    """The old window *appended* — a later call's prompt was the earlier
    one plus more. This mechanism does not append at all: the summary is
    replaced wholesale on the summariser's own schedule, and the turn is
    never a running list. What survives between two calls is the summary
    section byte-for-byte, not a growing shared prefix — the property is
    stronger, not merely relocated."""
    from friday.triage.context import LightContext
    from friday.triage.prompt import build_input

    room = summary_row(topic="the reelme wrapper api")

    said_a = build_input(LightContext(turn=[make_event(message_id="1", text="api lỗi")], summary=room))
    said_b = build_input(
        LightContext(
            turn=[make_event(message_id="2", text="a completely different report")],
            summary=room,
        )
    )

    prefix_a = said_a.split("<conversation>")[0]
    prefix_b = said_b.split("<conversation>")[0]
    assert prefix_a == prefix_b


def test_the_shared_prefix_between_two_triage_calls_is_almost_the_whole_prompt():
    """The measurability criterion itself, carried over: not merely asserted
    to be stable, but measured — against what a provider's cache actually
    sees, which is instructions *and* the per-call input together, not
    `build_input` in isolation. Instructions are the identity, the job, how
    to think, and the operator's examples, all held fixed across every call
    an agent makes; only the per-call input's turn was ever meant to change.

    `relevant_messages`'s version built 24 lines of noise between two calls
    and checked the ratio against a hand-rolled prompt stand-in; this one
    holds the summary fixed — what the DAG's own rebuild schedule guarantees
    in production — and varies only the turn."""
    from friday.triage.context import LightContext
    from friday.triage.prompt import build_input

    instructions = Triage(config=CONFIG)._run.instructions
    room = summary_row(
        topic="the reelme wrapper api",
        facts=["test.apero is the staging host"],
        decisions=["traces are looked up by x-request-id"],
    )

    early = instructions + build_input(
        LightContext(turn=[make_event(message_id="1", text="api lỗi")], summary=room)
    )
    late = instructions + build_input(
        LightContext(
            turn=[make_event(message_id="99", text="a much later, unrelated mention")],
            summary=room,
        )
    )

    shorter = min(len(early), len(late))
    matched = 0
    while matched < shorter and early[matched] == late[matched]:
        matched += 1
    ratio = matched / shorter if shorter else 1.0

    assert ratio > 0.9


# --- board `every-answer-has-a-shape`, ticket 07: one closed set (D6) --------


async def test_a_classification_is_the_return_value_of_the_call_that_asked():
    """Nothing is read off a capture. `Triage.decide` returns what the model
    said, validated, and reading the code is enough to see where the answer
    comes from — which it was not while a tool wrote into a per-run object the
    caller read back afterwards."""
    from friday.sdk.testing import ScriptedModel, function_call

    triage = triage_with(
        [function_call("answer", {"type": "api_issue", "confidence": 0.9}, call_id="1")]
    )

    assert await decide(triage) == Decided(type="api_issue", confidence=0.9)


async def test_skip_is_a_member_of_the_same_set_and_is_validated_the_same_way():
    """D6 reverses the `classify`/`skip` split. The two were validated
    differently because they were two tools; "there is no work here" is now
    checked exactly as strictly as "there is"."""
    from friday.sdk.testing import ScriptedModel, function_call

    triage = triage_with(
        [function_call("answer", {"type": "skip", "confidence": 0.95}, call_id="1")]
    )

    assert await decide(triage, text="anyone want lunch") == Decided(
        type="skip", confidence=0.95
    )


async def test_a_type_outside_the_closed_set_never_becomes_a_classification():
    """The failure this change exists to make impossible. An invented type
    could reach `TriageRunner._apply` and open a task the pool then discovers
    has no graph — money spent finding out what the schema already knew.

    Measured, the wire does not stop it: asked for a closed enum, the
    configured provider returned `hardware_issue`. What stops it is the
    validation in this process.
    """
    from friday.sdk.testing import ScriptedModel, function_call

    triage = triage_with(
        [function_call("answer", {"type": "hardware_issue", "confidence": 0.9}, call_id="1")],
        [function_call("answer", {"type": "hardware_issue", "confidence": 0.9}, call_id="2")],
    )

    outcome = await decide(triage)

    assert isinstance(outcome, NeedsHuman)
    assert outcome.out_of_set, (
        "an invented type and a wrong-but-real one are different failures"
    )


async def test_an_invented_type_earns_the_same_one_correction_as_anything_else():
    """It is a bad tool call like any other, so it comes back as the tool's own
    output naming the field, and the model gets the turn `max_turns` allows."""
    from friday.sdk.testing import ScriptedModel, function_call

    triage = triage_with(
        [function_call("answer", {"type": "hardware_issue", "confidence": 0.9}, call_id="1")],
        [function_call("answer", {"type": "api_issue", "confidence": 0.8}, call_id="2")],
    )

    assert await decide(triage) == Decided(type="api_issue", confidence=0.8)


async def test_a_provider_that_never_answered_is_not_an_invented_type():
    """Two failures that both leave no classification, told apart because they
    lead different places: one is worth reporting as the model inventing a
    label, the other is an outage."""
    triage = triage_with([assistant_message("")])

    outcome = await decide(triage)

    assert isinstance(outcome, NeedsHuman)
    assert not outcome.out_of_set


def test_every_decision_the_model_may_name_carries_its_own_description():
    """A tool parameter is an instruction to the model, and an enum member
    nobody defined is an instruction to guess. Each type's description is read
    from its own `Params` class docstring — so a fourth type is a fourth class,
    not a class and a second description of it somewhere else.

    `skip`'s line is written by hand, because it is the one decision with no
    class behind it. It was a whole second tool for that reason; D6 is the
    finding that a tool is an expensive way to hold one sentence.

    This replaces `test_create_task_describes_every_type_from_its_own_params_class`,
    which asserted the same thing about `classify`'s enum.
    """
    from friday.agent.harness import _answer_params
    from friday.dag import registry
    from friday.domain.actions import make_decided

    # The closed set now lives on the boot-built schema (ticket 11), not on the
    # `Decided` value type — `make_decided` builds it from the registry's types.
    params = registry.decision_params()
    described = _answer_params(make_decided(params))["properties"]["type"]

    assert set(described["enum"]) == set(registry.decisions())
    for name, params_cls in params.items():
        # Whitespace collapsed, the way `_means` renders it: a definition long
        # enough to be worth writing is wrapped in the source, and the enum
        # keeps one line per label.
        assert " ".join(params_cls.__doc__.split()) in described["description"], name
    assert "skip" in described["description"]


def test_triage_is_never_asked_for_anything_but_a_type_and_a_confidence():
    """The line, held by the only thing that can hold it.

    A tool parameter is an instruction to the model, so a schema with
    `correlation_id` in it *is* triage extracting, whatever the prompt says.
    Adding one back would put two producers on one field again — and the merge
    that reconciled them cost nineteen direct messages about one report before
    it was removed. Lifting values out of the message is
    `friday/extraction/`'s job, with its own failure mode.

    **Asserted over the agent this really builds**, not over a tool list
    imported by name: triage's tools used to be a module constant a test could
    read, and now they are whatever `Harness(answers=Decided)` wires, so
    reading the construction is the only way to be sure nothing else was
    handed to it.
    """
    triage = triage_with()

    for tool in triage._run.tools:
        asked = set(tool.params_json_schema.get("properties", {}))
        assert asked <= {"type", "confidence"}, f"{tool.name} also asks for {asked}"


def test_a_task_type_that_never_wrote_down_what_it_means_is_refused_at_import():
    """An enum member nobody defined is an instruction to guess, and the guess
    is on the highest-volume path in the system.

    **The hazard is not an empty docstring, which cannot happen.** A
    `@dataclass` always has one: absent its own, Python synthesises the
    constructor signature, so a forgetful author ships
    `ApiIssueParams(summary: str = '', environment: str | None = None, ...)`
    to the model as the description of what the type *means*. It reads like a
    description to everything except a person. Written after a guard against
    the empty case was found unable to fire.
    """
    from dataclasses import dataclass

    from friday.domain.actions import _means

    @dataclass
    class Undocumented:
        summary: str = ""

    assert Undocumented.__doc__, "Python synthesised one; that is the point"

    with pytest.raises(ValueError, match="constructor signature"):
        _means("mystery", Undocumented)


async def test_an_answer_that_names_no_type_is_never_silently_a_skip():
    """**The bug this whole board exists to kill, reintroduced in triage and
    found by review.**

    `skip` opens nothing: `TriageRunner._apply` logs a debug line and returns,
    so the mention leaves the queue and nobody ever sees it. That makes `skip`
    the one decision that must never be reachable by accident — and giving
    `Decided.type` a default of `skip` made it the decision the system reaches
    when the model says *nothing at all*. `fits` drops unknown keys, every
    remaining field had a default, and an empty answer validated cleanly into a
    silent discard.

    Which is CLAUDE.md's "Never drop a mention... never to a silent discard",
    and it is the same shape as the failure this board was opened for: an
    unreadable answer becoming a successful one because every field had a
    default.
    """
    from friday.sdk.testing import ScriptedModel, function_call

    nothing = {}
    triage = triage_with(
        [function_call("answer", nothing, call_id="1")],
        [function_call("answer", nothing, call_id="2")],
    )

    outcome = await decide(triage, text="the api is 500ing")

    assert isinstance(outcome, NeedsHuman), (
        "a model that named no type produced a silent discard"
    )


async def test_the_tools_own_former_parameter_name_is_not_a_skip_either():
    """The realistic shape of the same bug, and the reason a default was
    dangerous rather than merely untidy: `task_type` is what the deleted
    `classify` tool called this field, so a model carrying that habit — from a
    few-shot example, from its own training, from a prompt someone half
    updated — named a real type and had it dropped.

    Unknown keys are dropped by design (that tolerance is load-bearing and
    older than this board), so the only thing standing between a stale field
    name and a lost mention is `type` having no default.
    """
    from friday.sdk.testing import ScriptedModel, function_call

    stale = {"task_type": "api_issue", "confidence": 0.9}
    triage = triage_with(
        [function_call("answer", stale, call_id="1")],
        [function_call("answer", stale, call_id="2")],
    )

    assert isinstance(await decide(triage), NeedsHuman)


def test_both_of_the_fields_triage_answers_are_required():
    """Asserted on the schema as well as through the run, because this is the
    guard and a guard that only holds by accident of another test's scripting
    is not one."""
    from friday.agent.harness import _answer_params

    schema = _answer_params(Decided)

    assert set(schema.get("required", ())) == {"type", "confidence"}


async def test_a_malformed_confidence_is_not_reported_as_an_invented_type():
    """D20 splits one failure into two numbers, and the split has to be true
    of each of them: the report says "the model named a type that does not
    exist", so it must not say it about a model that named a real type and
    then wrote nonsense in the other field.

    Found by review. The signal fired on *any* validation failure, so a
    confidence of `"very high"` — which is a formatting mistake, not an
    invented decision — was counted as an invented type and printed under that
    sentence.
    """
    from friday.sdk.testing import ScriptedModel, function_call

    wrong_shape = {"type": "api_issue", "confidence": "very high"}
    triage = triage_with(
        [function_call("answer", wrong_shape, call_id="1")],
        [function_call("answer", wrong_shape, call_id="2")],
    )

    outcome = await decide(triage)

    assert isinstance(outcome, NeedsHuman)
    assert not outcome.out_of_set, (
        "a malformed confidence was reported as an invented task type"
    )


def test_the_prompt_describes_the_one_tool_that_exists():
    """**The board's own "door that is not in the room" failure, on the prompt
    where it costs most.**

    Triage answered by *choosing between two tools* — the label was which tool
    got called — and now answers by naming a value in one tool's argument.
    D6 changed the mechanism; the prompt went on describing the old one, and a
    model told to pick between tools that are not there improvises.

    This is the highest-volume prompt in the system and CLAUDE.md already
    records one instance of it costing 79% of the prompt on instructions for
    something the agent could not do. Pinned on the assembled instructions
    rather than on a constant, so rewording any one of the pieces cannot lose
    it.
    """
    from friday.triage.prompt import build_instructions

    built = build_instructions()

    assert "which tool you call is the answer" not in built.lower(), (
        "the prompt still says the label is which tool was called"
    )
    assert "not two" not in built.lower(), (
        "the prompt still warns against calling two tools; there is one"
    )


async def test_a_model_that_named_nothing_did_not_name_an_invented_type():
    """D20's number has to be true of every row it counts. An empty answer
    names no type — it is a model that said nothing, which is the same class of
    non-answer as a provider that never replied.

    Found by review, twice: the flag first fired on any validation failure,
    then on any failure of the `type` field, and only this version distinguishes
    a value the model *sent* from one it left out."""
    from friday.sdk.testing import ScriptedModel, function_call

    triage = triage_with(
        [function_call("answer", {}, call_id="1")],
        [function_call("answer", {}, call_id="2")],
    )

    outcome = await decide(triage)

    assert isinstance(outcome, NeedsHuman)
    assert not outcome.out_of_set, "an empty answer was counted as an invented type"


async def test_a_real_type_under_the_deleted_tools_old_key_is_not_invented_either():
    """`task_type` is what `classify` called this field. A model carrying that
    habit named `api_issue` — a real member of the set — under a key this shape
    cannot see. It still reaches a person, and the mention is still not
    dropped, but calling it an invented type would be false."""
    from friday.sdk.testing import ScriptedModel, function_call

    stale = {"task_type": "api_issue", "confidence": 0.9}
    triage = triage_with(
        [function_call("answer", stale, call_id="1")],
        [function_call("answer", stale, call_id="2")],
    )

    outcome = await decide(triage)

    assert isinstance(outcome, NeedsHuman)
    assert not outcome.out_of_set


async def test_a_type_the_model_actually_invented_is_still_counted():
    """The other side of the line, so the narrowing above cannot have quietly
    turned the number off."""
    from friday.sdk.testing import ScriptedModel, function_call

    invented = {"type": "hardware_issue", "confidence": 0.9}
    triage = triage_with(
        [function_call("answer", invented, call_id="1")],
        [function_call("answer", invented, call_id="2")],
    )

    assert (await decide(triage)).out_of_set


async def test_an_accented_word_is_not_a_different_accented_word():
    """Folding diacritics away was meant to catch `luong` written without
    them. It also made `luồng` (a flow) the same word as `lương` (salary),
    and `thường` (usual) the same as `thưởng` (bonus) — both everyday words
    here. Measured on the operator's own channel, 2026-09-20: "tài liệu về
    luồng duyệt task ở đâu vậy" and "không có gì bất thường" were held from
    the model and the reporters got silence.

    The rule the folding is for still holds: a message typed without
    diacritics still matches a listed word that has them.
    """
    triage, model = guarded()

    for innocent in (
        "tài liệu về luồng duyệt task ở đâu vậy",
        "không có gì bất thường",
        "luồng thanh toán Midas lỗi rồi",
    ):
        assert WORDS.found(innocent) is None, innocent

    for held in ("lương tháng này về chưa", "luong thang nay ve chua", "thuong tet"):
        assert WORDS.found(held) is not None, held

    # A message mixes the two spellings freely, so the question is about the
    # word that matched, not about the message.
    assert WORDS.found("luồng thanh toan bị lỗi") is None
    assert WORDS.found("luong thanh toán về chưa") == "lương"
    assert WORDS.found("Mật Khẩu của e sai") == "mật khẩu"
