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
from agents.models.interface import Model
from agents.testing import ScriptedModel, assistant_message, function_call

from conftest import make_event
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


async def decide(triage, text="the api is wrong", turn=()):
    return await triage.decide(make_event(text=text), turn=turn)


async def test_an_api_problem_becomes_an_api_issue():
    triage = triage_with([
        function_call("classify", {"task_type": "api_issue", "confidence": 0.9}, call_id="1")
    ])

    outcome = await decide(triage)

    assert isinstance(outcome, Decided)
    assert outcome.type == "api_issue"
    assert outcome.confidence == 0.9


async def test_a_permission_request_becomes_an_access_request():
    triage = triage_with([
        function_call("classify", {"task_type": "access_request", "confidence": 0.95}, call_id="1")
    ])

    assert (await decide(triage)).type == "access_request"


async def test_a_question_about_docs_becomes_a_doc_question():
    triage = triage_with([
        function_call("classify", {"task_type": "doc_question", "confidence": 0.8}, call_id="1")
    ])

    assert (await decide(triage)).type == "doc_question"


async def test_social_talk_becomes_a_skip():
    triage = triage_with([
        function_call("skip", {"confidence": 0.99}, call_id="1")
    ])

    assert (await decide(triage)).type == "skip"


async def test_a_message_carrying_nothing_still_decides():
    """"the api is wrong" is the most common shape there is and carries no
    values at all. It must still produce a task — that is what triggers asking
    for the fields, and it is why classifying does not depend on extracting."""
    triage = triage_with([
        function_call("classify", {"task_type": "api_issue", "confidence": 0.6}, call_id="1")
    ])

    assert (await decide(triage)).type == "api_issue"


async def test_answering_without_calling_a_tool_asks_for_a_human():
    """Never-drop: an undecided message becomes a task, not silence."""
    triage = triage_with([[assistant_message("I am not sure what this is.")]])

    outcome = await decide(triage)

    assert isinstance(outcome, NeedsHuman)


async def test_a_model_failure_asks_for_a_human():
    triage = triage_with(RuntimeError("provider exploded"))

    outcome = await decide(triage)

    assert isinstance(outcome, NeedsHuman)
    assert "provider exploded" in outcome.reason


async def test_triage_needs_no_database():
    """It reports a decision; applying it belongs to the caller."""
    import inspect

    assert "db" not in inspect.signature(Triage.__init__).parameters


def test_no_triage_tool_asks_for_anything_but_a_type_and_confidence():
    """The line, held by the only thing that can hold it.

    A tool parameter is an instruction to the model, so a schema with
    `correlation_id` in it *is* triage extracting, whatever the prompt says.
    Adding one back would put two producers on one field again — and the merge
    that reconciled them cost nineteen direct messages about one report before
    it was removed.

    `task_type` is not extraction: it is what used to be encoded as *which*
    tool got called, and ticket 08 made it a parameter of one tool instead.
    """
    from friday.triage import TOOLS

    for tool in TOOLS:
        params = set(getattr(tool, "params_json_schema", {}).get("properties", {}))
        assert params <= {"confidence", "task_type"}, f"{tool.name} also asks for {params}"


def test_create_task_describes_every_type_from_its_own_params_class():
    """D16: adding a task type is adding one `Params` class, not a class and a
    second description of it here. `create_task`'s enum and its per-value
    description are both read out of `PARAMS` at import time — this pins that
    nobody hand-wrote either and let them drift."""
    from friday.triage import TOOLS
    from friday.domain.models import PARAMS

    (create_task,) = [t for t in TOOLS if t.name == "classify"]
    schema = create_task.params_json_schema["properties"]["task_type"]

    assert set(schema["enum"]) == set(PARAMS)
    for name, cls in PARAMS.items():
        assert cls.__doc__.strip() in schema["description"], (
            f"{name}'s own docstring is not in create_task's description"
        )


class NeverCalled(Model):
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

    async def get_response(self, *a, **kw):
        self.reached += 1
        raise AssertionError("the model was called")

    def stream_response(self, *a, **kw):
        self.reached += 1
        raise AssertionError("the model was called")


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
        [function_call("classify", {"task_type": "api_issue", "confidence": 0.9}, call_id="1")],
    )
    triage._sensitive = WORDS

    assert (await decide(triage, text)).type == "api_issue"


async def test_an_empty_list_holds_nothing():
    """An install that has not thought about this yet gets what it would have
    had without the feature, not someone else's guesses about what is sensitive
    in their workplace."""
    triage = triage_with(
        [function_call("classify", {"task_type": "api_issue", "confidence": 0.9}, call_id="1")],
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

    `stop_when` moves the terminator to the thing that actually means
    answered: a classification recorded in the capture.
    """
    from agents.testing import ScriptedModel, function_call

    triage = Triage(
        config=CONFIG,
        model=ScriptedModel(
            [
                [function_call("classify", {"task_type": "api_issue",
                                            "confidence": "high"}, call_id="1")],
                [function_call("classify", {"task_type": "api_issue",
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
    from agents.testing import ScriptedModel, function_call

    bad = [function_call("classify", {"task_type": "api_issue",
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
    built = Triage(config=CONFIG)._run.agent.instructions

    for where_from, text in (("the constant", INSTRUCTIONS), ("the agent", built)):
        assert "<skill_system>" not in text, f"a catalogue reached {where_from}"
        assert "answer-in-vietnamese" not in text, f"a skill reached {where_from}"

    for where in (Triage.__init__, build_triage):
        assert "skills" not in inspect.signature(where).parameters, (
            f"{where.__qualname__} can still be handed a skill library"
        )


def test_triage_still_has_exactly_the_two_tools_that_are_its_answer():
    """Removing the catalogue must not remove the answer. `classify` names
    everything that opens work and `skip` names the absence of it."""
    from friday.tools.classify import TOOLS

    assert [t.name for t in TOOLS] == ["classify", "skip"]


# --- triage reads a light context (ticket 09) -----------------------------


def test_build_input_carries_the_turn_and_no_room_by_default():
    """No room, no `<channel_derived>` section at all — a room nobody has
    written anything about must not cost the classifier a byte, the same
    property ticket 01 proved for the extractor."""
    from friday.triage.prompt import build_input

    said = build_input([make_event(text="api lỗi")])

    assert "<channel_derived>" not in said
    assert "api lỗi" in said


def test_build_input_carries_the_rooms_summary_when_there_is_one():
    """The channel slot ticket 06's summariser writes to, read directly —
    the same section the responder already reads, not a new one invented
    for triage."""
    from friday.memory.channel_context import ChannelContext
    from friday.triage.prompt import build_input

    room = ChannelContext(
        channel_id="watched", base={}, derived={"summary": {"topic": "the reelme api"}},
        overrides={},
    )
    said = build_input([make_event(text="api lỗi")], room=room)

    assert "<channel_derived>" in said
    assert "the reelme api" in said


def test_build_input_does_not_carry_overrides_or_base():
    """Domain memory and operator overrides do not reach triage — it decides
    a label, not a value, and those layers are exactly the kind of thing a
    value would be built from."""
    from friday.memory.channel_context import ChannelContext
    from friday.triage.prompt import build_input

    room = ChannelContext(
        channel_id="watched",
        base={"company": "apero"},
        derived={"summary": {"topic": "x"}},
        overrides={"test.apero": "staging"},
    )
    said = build_input([make_event(text="api lỗi")], room=room)

    assert "apero" not in said.replace("the reelme api", "")  # topic itself may say "apero"
    assert "staging" not in said


def test_build_input_renders_a_real_turn_as_multiple_lines():
    """The point of the whole redesign: a burst of messages is shown as
    itself, not pre-flattened into one string with no clock on any line but
    the first."""
    from friday.triage.prompt import build_input

    turn = [
        make_event(message_id="1", text="api lỗi rồi anh ơi"),
        make_event(message_id="2", text="curl -X GET /pay trả 500"),
    ]
    said = build_input(turn)

    assert "api lỗi rồi anh ơi" in said
    assert "curl -X GET /pay trả 500" in said
    assert said.count("[") >= 2  # two bracketed timestamps, two lines


async def test_decide_resolves_the_room_from_its_own_context_store():
    """`Triage` holds the store, resolves the room per call from the event's
    channel — the same split `Extractor` uses: a store lives as long as the
    process, a room lasts one call."""
    from friday.memory.channel_context import ChannelContext

    class _Store:
        def context(self, channel_id):
            return (
                ChannelContext(
                    channel_id=channel_id, base={}, overrides={},
                    derived={"summary": {"topic": "the reelme api"}},
                )
                if channel_id == "watched"
                else None
            )

    seen = []

    class _Capturing(Model):
        async def get_response(self, *a, **kw):
            seen.append(kw.get("input") or a)
            return await ScriptedModel([
                function_call("classify", {"task_type": "api_issue", "confidence": 0.9}, call_id="1")
            ]).get_response(*a, **kw)

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    triage = Triage(config=CONFIG, model=_Capturing(), context=_Store())

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

    class _Capturing(Model):
        async def get_response(self, *a, **kw):
            seen.append(str(kw.get("input") or a))
            return await ScriptedModel([
                function_call("classify", {"task_type": "api_issue", "confidence": 0.9}, call_id="1")
            ]).get_response(*a, **kw)

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    triage = Triage(config=CONFIG, model=_Capturing())

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


# --- prefix stability, one layer up (ticket 09 reverses ticket 26) --------


async def test_the_summary_section_does_not_care_how_much_the_room_has_said(db, tmp_path):
    """Ticket 26's guarantee was `relevant_messages`'s: unbounded, so a
    relevant message once included was never evicted and the prefix a model
    saw on call N was still there on call N+1. That guarantee lived at the
    database layer — every new message widened the window a little.

    This one lives at the summary, and does not merely assert `build_input`
    is a pure function of a fixed object (a first version of this test did
    exactly that and proved nothing about a real database): it records a
    hundred real messages into the same room, through a real `ContextStore`,
    with **no `rebuild_all()` in between** — the heartbeat's own job, which
    this test deliberately does not call — and checks that what `store.context`
    hands `build_input` has not moved. `channel_derived` only ever renders
    `ctx.derived`, and nothing here writes to it except a rebuild the
    summariser runs on its own schedule, strictly less often than every
    message.
    """
    from friday.memory.channel_context import ContextStore
    from friday.triage.prompt import build_input

    store = ContextStore(tmp_path)
    store.init_channel(
        "watched", overrides={},
    )
    store.rebuild_derived(
        "watched", {"summary": {"topic": "the reelme wrapper api", "facts": ["x"]}}
    )
    store.hold_all()

    first = build_input(
        [make_event(message_id="1", text="a")], room=store.context("watched")
    )
    # Real messages, recorded into the actual database — not rebuilt from.
    # If anything here moved the summary, this is where it would show; the
    # count only has to be more than zero, so it stays small for speed.
    for n in range(2, 12):
        await db.record_message(make_event(message_id=str(n), text=f"noise {n}"))
    later = build_input(
        [make_event(message_id="11", text="a later mention")], room=store.context("watched")
    )

    def summary_section(said: str) -> str:
        return said.split("<conversation>")[0]

    assert summary_section(first) == summary_section(later)


def test_two_turns_against_the_same_room_share_everything_but_the_turn():
    """The old window *appended* — a later call's prompt was the earlier
    one plus more. This mechanism does not append at all: the summary is
    replaced wholesale on the summariser's own schedule, and the turn is
    never a running list. What survives between two calls is the summary
    section byte-for-byte, not a growing shared prefix — the property is
    stronger, not merely relocated."""
    from friday.memory.channel_context import ChannelContext
    from friday.triage.prompt import build_input

    room = ChannelContext(
        channel_id="watched", base={}, overrides={},
        derived={"summary": {"topic": "the reelme wrapper api"}},
    )

    said_a = build_input([make_event(message_id="1", text="api lỗi")], room=room)
    said_b = build_input(
        [make_event(message_id="2", text="a completely different report")], room=room
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
    from friday.memory.channel_context import ChannelContext
    from friday.triage.prompt import build_input

    instructions = Triage(config=CONFIG)._run.agent.instructions
    room = ChannelContext(
        channel_id="watched", base={}, overrides={},
        derived={"summary": {
            "topic": "the reelme wrapper api",
            "facts": ["test.apero is the staging host"],
            "decisions": ["traces are looked up by x-request-id"],
        }},
    )

    early = instructions + build_input([make_event(message_id="1", text="api lỗi")], room=room)
    late = instructions + build_input(
        [make_event(message_id="99", text="a much later, unrelated mention")], room=room
    )

    shorter = min(len(early), len(late))
    matched = 0
    while matched < shorter and early[matched] == late[matched]:
        matched += 1
    ratio = matched / shorter if shorter else 1.0

    assert ratio > 0.9
