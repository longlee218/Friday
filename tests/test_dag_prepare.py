"""The api_issue workflow — deterministic branching, no model.

The rule this encodes is the most frequent real action there is: a report
arrives without the fields needed to trace it, and the first move is to ask.

Ticket 33 moved that rule from `plan_api_issue` into the last node of the
`api_issue` graph. These tests follow it there: they exercise
`prepare`, the node every graph shares, with no agents and no tool servers —
exactly the state a fresh install is in.
"""

from __future__ import annotations

from friday.sdk.workflow import Deps as DAGDeps, DAGState
from friday.kernel.domain.models import AccessRequestParams
from plugins.docs.params import DocQuestionParams
from plugins.devops.params import ApiIssueParams
from types import SimpleNamespace

from friday.sdk.actions import Ask, HandOver
from friday.kernel.dag.prepare import prepare


async def _decide(task_type, params):
    """The simple path, exactly as `build_simple_dag`'s node wires it —
    `prepare_node`'s `on_ready` is `plan_by_required_parameters`.

    There was a `plan()` doing this, and every test here called it. Production
    did not — the node calls these two and routes to a graph in between — so a
    convenience wrapper had become a second path that only tests took, which is
    how five tests came to hold an unreachable branch elsewhere in this file.

    No `context=` passed — every caller here wants "nothing to extract from",
    which is what `context=None` already means (board
    `what-the-room-already-knows`, ticket 15).
    """
    from friday.kernel.dag.prepare import plan_by_required_parameters, prepare

    params, problem = await prepare(task_type, params)
    return problem or plan_by_required_parameters(task_type, params)


def params(**kw):
    return ApiIssueParams(summary="checkout is 500", **kw)


CID = "abcdef01-2345-6789-abcd-ef0123456789"


async def gate(**kw):
    """What the *route* decides — which is where this is decided.

    These used to drive `_compose_reply` directly and assert on the `Ask` it
    returned. That `Ask` is gone: the rule moved into `prepare()`, so a report
    with nothing to trace on never reaches a node at all. Asserting on the node
    was asserting on a path production stopped taking.
    """
    _, problem = await prepare("devops.api_issue", params(**kw))
    return problem


async def test_a_report_with_nothing_to_trace_on_asks_for_details():
    action = await gate()

    assert isinstance(action, Ask)
    assert "curl" in action.text.lower() or "endpoint" in action.text.lower()


async def test_a_correlation_id_alone_does_not_reach_the_graph():
    """Ticket 01 reversed this test, which read "a correlation id is enough".
    It is not: the operator never receives one from a reporter, and an id on
    its own says nothing about which host the request went to — `Resolve`
    hands over on exactly that. Keeping it as findability meant the type
    asked a reporter for the one thing they do not have."""
    assert isinstance(await gate(correlation_id=CID), Ask)


async def test_a_curl_is_enough_to_reach_the_graph():
    assert await gate(curl="curl https://x") is None


async def test_an_endpoint_with_an_identifier_is_enough_to_reach_the_graph():
    """The report the old rule turned away: "login API, deviceId X, 500" names
    a request the log can be searched for, and asking that reporter for a
    correlationId asks them to do a lookup they do not know how to do."""
    assert await gate(endpoint="/v1/login", identifier="device-42") is None


async def test_an_endpoint_without_an_identifier_is_not_enough():
    """The path alone matches every caller of it — measured on the production
    case of 2026-09-21, ten of the eighteen lines it returned were other
    people's successful requests."""
    assert isinstance(await gate(endpoint="/v1/login"), Ask)


async def test_an_environment_alone_is_not_enough_to_trace():
    """You cannot find a request from the environment name."""
    assert isinstance(await gate(environment="production"), Ask)


async def test_staging_is_not_an_environment_this_room_serves():
    """`staging` was in the enum until ticket 01 and no project has one. The
    only thing the word could do was let a reporter's guess validate cleanly
    and send `Resolve` looking for the logs of somewhere that does not
    exist."""
    action = await gate(environment="staging", curl="curl https://x")

    assert isinstance(action, Ask)
    assert "dev, production" in action.text


# ---- the other task types --------------------------------------------------


async def test_an_access_request_without_a_project_asks_for_one():
    """Same gap as api_issue had: a task that cannot be acted on has to say so,
    not sit in a queue nobody is watching."""
    action = await _decide(
        "access_request", AccessRequestParams(project="", permission="write",
                                              summary="needs access")
    )

    assert isinstance(action, Ask)
    assert "project" in action.text


async def test_a_doc_question_without_a_question_asks_for_one():
    action = await _decide("doc_question", DocQuestionParams(question="", doc_ref=None))

    assert isinstance(action, Ask)


async def test_an_optional_parameter_is_never_asked_for():
    """`doc_ref` is optional by its type. Asking for it would be asking for
    something we said we did not need."""
    action = await _decide("doc_question", DocQuestionParams(question="how does X work?",
                                                    doc_ref=None))

    assert isinstance(action, HandOver)


async def test_a_complete_request_hands_over_because_nothing_can_act_on_it_yet():
    """Nothing grants access. Handing over is honest; asking again would not be."""
    action = await _decide(
        "access_request",
        AccessRequestParams(project="backend", permission="write", summary="s"),
    )

    assert isinstance(action, HandOver)


async def test_the_summary_is_never_asked_for():
    """The model writes it. Asking the reporter for a summary of their own
    message is nonsense."""
    action = await _decide(
        "access_request", AccessRequestParams(project="backend",
                                              permission="write", summary="")
    )

    assert isinstance(action, HandOver)


async def test_api_issue_keeps_its_own_rule():
    """An endpoint plus one id makes a request findable; its type cannot say
    that — every field of it is `str | None`, so the annotations call them all
    optional and the report unusable either way."""
    traceable = ApiIssueParams(
        summary="s",
        environment=None,
        endpoint="/v1/login",
        identifier="device-42",
        curl=None,
    )

    assert isinstance(await _decide("devops.api_issue", traceable), HandOver)


async def test_a_malformed_correlation_id_is_caught_by_the_rule():
    """A value that does not look like a uuid is rejected before dispatch.

    Ticket 31 adds InSet/Matches rules to ApiIssueParams; ticket 30 wired
    validate into plan(). Together they catch what triage left through that
    the structural check did not: not 'field is missing', but 'field is
    wrong'."""
    bad = ApiIssueParams(summary="s", correlation_id="abc-123")

    action = await _decide("devops.api_issue", bad)

    assert isinstance(action, Ask)
    assert "uuid" in action.text


# ---- ask_clarification: code stays the floor (D12) --------------------------


async def _prepare_with_clarify(params_obj, clarify, *, extracted=None, monkeypatch):
    """`prepare()` with `extract()` stood in for, so the ordering between
    code's own floor and a model's `Clarify` can be tested without a real
    extractor or model."""
    import friday.kernel.dag.prepare as wf
    from tests.test_extraction import _context

    async def stub_extract(task_type, context, *, task_id=None, node=None):
        return extracted, clarify

    monkeypatch.setattr(wf, "_extract", stub_extract)
    return await wf.prepare(
        "devops.api_issue", params_obj, context=_context("irrelevant", type(params_obj))
    )


async def test_code_floor_wins_over_a_clarify_that_names_a_different_field(monkeypatch):
    """The extractor asked about `environment`, but `correlation_id` is
    malformed — code's own rule is what the reporter is challenged with,
    because a value the rules reject cannot be waved through by the model
    having asked about something else instead."""
    from friday.kernel.extraction import Clarify

    bad = params(correlation_id="not-a-uuid")
    clarify = Clarify(fields=("environment",), because="no server named")

    _, action = await _prepare_with_clarify(bad, clarify, monkeypatch=monkeypatch)

    assert isinstance(action, Ask)
    assert "uuid" in action.text
    assert "server" not in action.text, "the model's own wording must not leak in here"


async def test_a_clarify_for_an_already_filled_field_is_not_honoured(monkeypatch):
    """The model asked about `curl`, but it is already there — from the
    reporter, or from this same extraction run. Asking again for something
    already answered is not a question this exists to ask."""
    from friday.kernel.extraction import Clarify

    complete = params(curl="curl https://x/y")
    clarify = Clarify(fields=("curl",), because="not sure")

    _, action = await _prepare_with_clarify(complete, clarify, monkeypatch=monkeypatch)

    assert action is None, "nothing left to ask about once the field is filled"


async def test_a_clarify_becomes_an_ask_once_code_has_nothing_to_say(monkeypatch):
    """A report with a curl is traceable — code's own rules find nothing
    wrong — but the extractor read something worth asking about anyway.
    That is the case `ask_clarification` exists for."""
    from friday.kernel.extraction import Clarify

    traceable = params(curl="curl -X GET /pay")
    clarify = Clarify(fields=("environment",), because="curl doesn't say which server")

    _, action = await _prepare_with_clarify(traceable, clarify, monkeypatch=monkeypatch)

    assert isinstance(action, Ask)
    assert "environment" in action.text.lower()


# ---- the end of the simple path --------------------------------------------


async def test_plan_by_required_parameters_hands_over_once_everything_is_present():
    """`plan_by_required_parameters` on its own, once `prepare` has left
    nothing outstanding: no question left to ask, and hand-over is what
    "a human takes it from here" looks like."""
    action = await _decide(
        "devops.api_issue", ApiIssueParams(summary="s", curl="curl https://x/y")
    )

    assert isinstance(action, HandOver)


# --- a later extraction fills blanks; it does not revise what it said --------


def test_a_second_extraction_does_not_reword_the_first():
    """This runs again on every follow-up, and a model asked the same question
    twice does not give the same answer. Letting the second run rewrite the
    first cost nineteen direct messages about one report, each carrying a
    differently worded summary — a reworded value is a *changed* value, so the
    graph discarded its work and the operator was told again."""
    from friday.kernel.dag.prepare import _fill

    filled = _fill(
        ApiIssueParams(summary="checkout is 500ing", environment="production"),
        ApiIssueParams(summary="User reports the API is failing", environment="prod"),
    )

    assert filled.summary == "checkout is 500ing"
    assert filled.environment == "production"


def test_a_later_extraction_fills_what_is_still_blank():
    """Which is exactly what a follow-up supplying the correlationId is, and
    the only way it reaches the task now that triage does not lift it out."""
    from friday.kernel.dag.prepare import _fill

    filled = _fill(
        ApiIssueParams(summary="checkout is 500ing"),
        ApiIssueParams(
            summary="ignored",
            correlation_id="abcdef01-2345-6789-abcd-ef0123456789",
            environment="production",
        ),
    )

    assert filled.correlation_id == "abcdef01-2345-6789-abcd-ef0123456789"
    assert filled.environment == "production"
    assert filled.summary == "checkout is 500ing"


def test_an_empty_string_counts_as_a_blank():
    """A field the model wrote as "" is not a value someone supplied."""
    from friday.kernel.dag.prepare import _fill

    assert _fill(
        ApiIssueParams(summary="s", environment=""),
        ApiIssueParams(summary="s", environment="production"),
    ).environment == "production"


async def test_a_refused_extractor_hands_over_instead_of_asking(monkeypatch):
    """A ceiling must not make the reporter answer for it.

    `CLAUDE.md` already names this failure — "it opens tasks with no
    parameters and asks the reporter for what they already said" — as the
    reason a task type without an extractor is *broken* rather than degraded.
    A ceiling on an extractor reproduces it exactly: the model is not called,
    no fields come back, the structural check finds them missing, and the
    reporter is asked for the correlationId they put in their first message.

    The difference between "the model could not answer" and "we declined to
    ask it" is the whole of what decides that. The first is worth a question;
    the second is worth telling the operator their budget stopped a task.
    """
    import friday.kernel.dag.prepare as wf
    from friday.kernel.harness.harness import Refused
    from friday.sdk.actions import HandOver
    from plugins.devops.params import ApiIssueParams
    from tests.test_extraction import _context

    async def refused(task_type, context, *, task_id=None, node=None):
        raise Refused("api_issue_extractor has spent 999 of its 10 tokens today")

    monkeypatch.setattr(wf, "_extract", refused)

    _, action = await wf.prepare(
        "devops.api_issue",
        ApiIssueParams(summary="checkout 500"),
        context=_context(
            "prod broke, correlationId abcdef01-2345-6789-abcd-ef0123456789",
            ApiIssueParams,
        ),
    )

    assert isinstance(action, HandOver)
    assert "tokens today" in action.reason


# --- node 0 pays once for one set of facts (ticket 04) --------------------


async def _reported(db, *, text="@Lee API lỗi rồi a ơi"):
    """A task with the reporter's message linked to it, which is what
    `original_text_for` reads and therefore what makes extraction run at all.
    """
    from conftest import make_event
    from tests.test_pool import _said, make_task

    task = await make_task(db)
    await _said(db, "m1", text, secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "devops.api_issue"}
    )
    return task


class _StandsForAnExtractor:
    """The half of `Extractor`'s contract a double has to keep.

    `would_ask` is not optional politeness: `input_fingerprint` asks the
    registered extractor what it would send, so a double that cannot answer
    would have to be handled by a fallback — and a fallback here would
    fingerprint something other than the prompt, which is the bug ticket 01's
    review found in the first place.

    Board `what-the-room-already-knows`, ticket 15, D26: `context` arrives
    already gathered — the room, the domain memories, the open questions and
    `known` are all fields of it now, not arguments a caller threads through.

    **Renders with `known` forced blank**, matching what this double always
    did before `known` reached `build_input` at all (ticket 08): it never
    threaded `known` through, so the schema it rendered never shrank for an
    already-filled field. Only `_UsesKnown` and `_RecordsWhatItWasShown`
    below care what `known` actually filters, and both render
    `context` unmodified instead of going through this default.
    """

    async def would_ask(self, context):
        from dataclasses import replace

        from friday.kernel.extraction.prompt import build_input

        return build_input(replace(context, known=type(context.known)()))


class _CountingExtractor(_StandsForAnExtractor):
    """Stands in for the model. Returns the same answer every time, which is
    the point: the same facts must not be paid for twice."""

    def __init__(self, params, clarify=None):
        self.params, self.clarify, self.texts = params, clarify, []

    async def run(self, context, *, task_id=None, node=None):
        self.texts.append(context.transcript)
        return self.params, self.clarify


async def test_node_0_pays_once_when_nothing_has_changed(db):
    """One task in the recorded data has two extractor calls of 1,790 input
    tokens whose prompts are byte-identical — same sha256, seven and a half
    hours apart. Node 0 is excluded from the checkpoint and re-executes on
    every pass, which is correct, because it must see a message that arrived
    since the last one. What it must not do is call a model when nothing did.
    """
    from friday.kernel.dag.prepare import prepare_node
    from tests.test_extraction import _install

    extractor = _CountingExtractor(ApiIssueParams(summary="checkout 500"))
    _install("devops.api_issue", extractor)
    task = await _reported(db)
    node = prepare_node("devops.api_issue", ApiIssueParams)

    first = await node.run(DAGState.empty(), DAGDeps(task=task, db=db))
    second = await node.run(
        DAGState.empty(), DAGDeps(task=await db.task(task.id), db=db)
    )

    assert len(extractor.texts) == 1, (
        f"extracted {len(extractor.texts)} times for one unchanged task"
    )
    assert second == first, "the second pass reached a different conclusion"


async def test_a_field_already_filled_drops_out_of_the_next_passs_schema(db):
    """Board `what-the-room-already-knows`, ticket 08, D8: "a field the
    parameter schema names is compacted into the task's parameters" — once
    `environment` is filled, the next pass does not pay to be told about it
    again, even though a new message keeps the fingerprint from replaying a
    stale mark."""
    from friday.kernel.dag.prepare import prepare_node
    from tests.test_extraction import _install
    from tests.test_pool import _said

    seen: list[str] = []

    class _RecordsWhatItWasShown(_StandsForAnExtractor):
        async def would_ask(self, context):
            from friday.kernel.extraction.prompt import build_input

            return build_input(context)

        async def run(self, context, *, task_id=None, node=None):
            # Node 0 rebuilds `context` from the task's current params on
            # every pass, so a field filled by the first run is already
            # dropped from the schema `context.known` carries into the
            # second — rendered here, unlike the base double, which forces
            # `known` blank.
            seen.append(await self.would_ask(context))
            return ApiIssueParams(environment="production"), None

    _install("devops.api_issue", _RecordsWhatItWasShown())
    task = await _reported(db)
    node = prepare_node("devops.api_issue", ApiIssueParams)

    await node.run(DAGState.empty(), DAGDeps(task=task, db=db))
    await _said(db, "m2", "curl -X POST /pay trả 500, trên production", secs=3)
    await node.run(DAGState.empty(), DAGDeps(task=await db.task(task.id), db=db))

    first, second = seen
    assert "- environment:" in first, "the field was never offered in the first place"
    assert "- environment:" not in second, "an already-filled field was shown again"


async def test_a_new_message_is_paid_for(db):
    """The reason node 0 re-runs at all. A reporter who sends the curl three
    seconds later must be read, so a changed transcript has to reach the
    model even though the parameters have not moved."""
    from friday.kernel.dag.prepare import prepare_node
    from tests.test_extraction import _install
    from tests.test_pool import _said

    extractor = _CountingExtractor(ApiIssueParams(summary="checkout 500"))
    _install("devops.api_issue", extractor)
    task = await _reported(db)
    node = prepare_node("devops.api_issue", ApiIssueParams)

    await node.run(DAGState.empty(), DAGDeps(task=task, db=db))
    await _said(db, "m2", "curl -X POST /pay trả 500, trên production", secs=3)
    await node.run(DAGState.empty(), DAGDeps(task=await db.task(task.id), db=db))

    assert len(extractor.texts) == 2, "a new message did not reach the extractor"
    assert "curl -X POST" in extractor.texts[-1]


async def test_a_question_the_extractor_raised_survives_the_skipped_call(db):
    """The subtle half. `Ask` here can come from the extractor's own
    `ask_clarification` rather than from a structural rule — `environment` is
    optional, so validation has nothing to say about it. Skipping the call
    without remembering what it asked would turn that `Ask` into "everything
    needed is here" on the very next pass, and hand the task over instead."""
    from friday.kernel.dag.prepare import prepare_node
    from friday.kernel.extraction import Clarify
    from tests.test_extraction import _install

    # A curl, so `_problems` is empty and the extractor's own question is
    # the only thing that can produce an `Ask`. The correlationId beside it
    # validates too, so neither rule has anything to say.
    # Without it the `_traceable` rule fires first and both passes return the
    # same code-written question — which is how the first version of this test
    # passed with the replay deleted.
    extractor = _CountingExtractor(
        ApiIssueParams(
            summary="service down",
            curl="curl https://api.aperogroup.ai/v1/pay",
            correlation_id="3f7a1e22-8b44-4c31-9d0e-77a2c6b51e90",
        ),
        clarify=Clarify(("environment",), "the URL says test, which is not an env"),
    )
    _install("devops.api_issue", extractor)
    task = await _reported(db)
    node = prepare_node("devops.api_issue", ApiIssueParams)

    first = await node.run(DAGState.empty(), DAGDeps(task=task, db=db))
    second = await node.run(
        DAGState.empty(), DAGDeps(task=await db.task(task.id), db=db)
    )

    assert isinstance(first, Ask), f"expected the extractor's question, got {first!r}"
    assert "environment" in first.text or "which environment" in first.text
    assert len(extractor.texts) == 1
    assert isinstance(second, Ask), (
        f"the remembered question was lost — the graph moved on with {second!r}"
    )
    assert second == first


async def test_the_fingerprint_is_the_prompt_so_every_input_counts():
    """It used to rebuild the two inputs node 0 knew about, and a review found
    what that costs: ticket 01 gave the prompt a third input — the room — the
    rebuild could not see, so an operator who wrote down what a room is got a
    task that never read it. It hashes what the extractor would actually send
    now, so a fourth input cannot be forgotten.

    Three things must move it: the reporter's words, the field schema, and the
    room."""
    from friday.kernel.extraction import input_fingerprint, registered
    from tests.test_extraction import _context, _install, _rows

    _install("fp_probe", _StandsForAnExtractor())
    try:
        text_a = await input_fingerprint("fp_probe", _context("API lỗi", ApiIssueParams))
        text_b = await input_fingerprint(
            "fp_probe", _context("API vẫn lỗi", ApiIssueParams)
        )
        with_room = await input_fingerprint(
            "fp_probe",
            _context(
                "API lỗi",
                ApiIssueParams,
                memories=_rows("env: staging"),
            ),
        )
    finally:
        registered().pop("fp_probe", None)

    assert text_a != text_b, "the reporter's words do not move the fingerprint"
    assert with_room != text_a, "the room does not move the fingerprint"
    assert await input_fingerprint(
        "no_such_type", _context("x", ApiIssueParams)
    ) == "", "an unregistered type should have nothing to remember"


async def test_known_moves_the_fingerprint_too():
    """Ticket 08's D8, the fifth input: a field getting filled shrinks the
    schema, which is a real change to the prompt — the fingerprint has to
    move with it, or a stale mark from before the field was filled would
    replay an answer built against a wider schema."""
    from friday.kernel.extraction import input_fingerprint, registered
    from tests.test_extraction import _context, _install

    class _UsesKnown(_StandsForAnExtractor):
        async def would_ask(self, context):
            from friday.kernel.extraction.prompt import build_input

            return build_input(context)

    _install("fp_probe", _UsesKnown())
    try:
        bare = await input_fingerprint("fp_probe", _context("API lỗi", ApiIssueParams))
        with_known = await input_fingerprint(
            "fp_probe",
            _context(
                "API lỗi", ApiIssueParams, known=ApiIssueParams(environment="production")
            ),
        )
    finally:
        registered().pop("fp_probe", None)

    assert bare != with_known, "an already-filled field did not move the fingerprint"


async def test_a_parameter_change_the_extractor_cannot_see_is_not_paid_for(db):
    """**A deliberate deviation from ticket 04's own criterion**, which asked
    for the task's parameters to count toward "has anything changed".

    They cannot. The extractor's input is `build_input(text, params_cls)` — the
    field schema and what the reporter wrote. Parameters never reach it, so a
    parameter that moved is not a reason to pay for the same answer again. The
    fill is replayed from the mark, so the outcome is the same either way."""
    from friday.kernel.dag.prepare import prepare_node
    from tests.test_extraction import _install

    extractor = _CountingExtractor(ApiIssueParams(summary="checkout 500"))
    _install("devops.api_issue", extractor)
    task = await _reported(db)
    node = prepare_node("devops.api_issue", ApiIssueParams)

    await node.run(DAGState.empty(), DAGDeps(task=task, db=db))
    # `summary` blanked and `environment` set: two parameter changes the
    # extractor cannot see. Blanking is what makes the replay observable —
    # `_fill` only fills blanks, so a mark that remembered nothing would leave
    # it blank while a mark that remembered the extraction puts it back.
    await db.set_task_params(
        task.id,
        {**(await db.task(task.id)).params, "summary": "", "environment": "staging"},
    )
    await node.run(DAGState.empty(), DAGDeps(task=await db.task(task.id), db=db))
    after = (await db.task(task.id)).params

    assert len(extractor.texts) == 1, "paid again for a change it cannot see"
    assert after["environment"] == "staging", "the parameter change was lost"
    assert after["summary"] == "checkout 500", (
        "the remembered extraction was not replayed into the blanked field"
    )


async def test_a_call_that_produced_nothing_is_not_remembered_as_an_answer(db):
    """`(None, None)` is not "the model found nothing" — a model that finds
    nothing still returns a `Params` with every field absent. It is the harness
    having swallowed a provider error into `last_error`, or output that missed
    the schema. Marking that would turn one 502 into a task that is never read
    again, against this repo's own "a hiccup is retried here and nowhere else".

    Found by review, not by the tests written with the feature."""
    from friday.kernel.dag.prepare import prepare_node
    from tests.test_extraction import _install

    class _Failing(_StandsForAnExtractor):
        def __init__(self):
            self.calls = 0

        async def run(self, context, *, task_id=None, node=None):
            self.calls += 1
            return None, None

    extractor = _Failing()
    _install("devops.api_issue", extractor)
    task = await _reported(db)
    node = prepare_node("devops.api_issue", ApiIssueParams)

    await node.run(DAGState.empty(), DAGDeps(task=task, db=db))
    await node.run(DAGState.empty(), DAGDeps(task=await db.task(task.id), db=db))

    assert extractor.calls == 2, "a failed call was remembered as an answer"
    assert await db.extraction_mark(task.id) is None, "marked an empty extraction"


async def test_a_room_fact_reaches_the_extractor_and_settles_the_field(db, tmp_path):
    """The tracer bullet, end to end: the operator writes down what
    `test.apero` is, and the extraction that asked about it stops asking.

    **What this proves and what it cannot.** The stub stands where the model
    stands, and it asserts the fact was in the prompt it was handed — so this
    proves the fact reached the decision and that the graph then asks nothing.
    Whether a real model uses a fact it can see is the model's business and no
    test here can settle it; that is what the recorded flow on the board is
    for.

    The recorded failure this replaces: `environment` came back null because
    the extractor could not tell which environment `test.apero` was, so it
    asked, and nobody answered.

    The room's fact used to say `staging`, which was a value only the room
    could put in the prompt and so a clean marker. Ticket 01 removed `staging`
    from the enum — no project this room serves has one — so the fact now says
    `dev` and the marker is the Vietnamese phrase around it, which nothing
    else in the prompt writes.
    """
    from friday.kernel.dag.prepare import prepare_node
    from friday.sdk.memory import MemoryOrigin
    from friday.kernel.domain.models import FridayState
    from tests.test_extraction import _install

    await db.memory_add(
        FridayState(channel_id="watched", agent="operator"),
        "test.apero chạy trên dev", kind="fact",
        origin=MemoryOrigin.ADMIN,
    )

    seen: list[str] = []

    class _ReadsTheRoom(_StandsForAnExtractor):
        async def run(self, context, *, task_id=None, node=None):
            # The room arrives already gathered, as the operator's rows in
            # `context.domain_memories`, read by `build_full_context` through
            # `deps.db` (board `read-it-the-way-the-operator-does`, ticket
            # 10) — this double does not resolve it itself.
            said = await self.would_ask(context)
            seen.append(said)
            # Asserted on the *value* and on the section, never on
            # `test.apero`: the reporter's own message says `test.apero`, so
            # the first version of this test passed with the room never
            # actually reaching the prompt. Only the room can put "chạy trên
            # dev" here — the bare word `dev` would not do, since the
            # `environment` field's own schema line names it.
            assert "[channel" in said, (
                f"no room section — rows were {context.domain_memories!r}"
            )
            assert "chạy trên dev" in said, (
                "the room's value never reached the prompt"
            )
            return (
                ApiIssueParams(
                    summary="service down",
                    environment="dev",
                    curl="curl https://test.apero/health",
                ),
                None,
            )

    _install("devops.api_issue", _ReadsTheRoom())
    task = await _reported(db, text="@Lee kiểm tra cho e curl sau https://test.apero/health")
    node = prepare_node("devops.api_issue", ApiIssueParams)

    outcome = await node.run(DAGState.empty(), DAGDeps(task=task, db=db))

    assert seen, "the extractor was never reached"
    assert not isinstance(outcome, Ask), f"still asking: {outcome!r}"
    assert (await db.task(task.id)).params["environment"] == "dev"


async def test_a_fact_written_after_the_first_pass_still_reaches_a_model(db, tmp_path):
    """The bug ticket 01 and ticket 04 made together, which neither had alone.

    Node 0 remembers what it extracted so it does not pay twice for the same
    facts. Ticket 01 put the room's own facts into the prompt. The fingerprint
    covered the reporter's text and the field schema, so writing a room fact
    did not change it: the mark replayed the stale answer, no model was called,
    and the fact never arrived.

    That is not a corner. It is the *only* case that matters, because the
    operator writes the fact **because** the task asked a question — so every
    task that would benefit has already extracted once and already has a mark.

    Reproduced with a probe before it was fixed: two passes, one model call,
    `did the new fact reach a model? False`.
    """
    from friday.kernel.dag.prepare import prepare_node
    from friday.sdk.memory import MemoryOrigin
    from friday.kernel.domain.models import FridayState
    from tests.test_extraction import _install

    asked: list[str] = []

    class _Watching(_StandsForAnExtractor):
        async def run(self, context, *, task_id=None, node=None):
            asked.append(await self.would_ask(context))
            return ApiIssueParams(summary="service down"), None

    _install("devops.api_issue", _Watching())
    task = await _reported(db)
    node = prepare_node("devops.api_issue", ApiIssueParams)

    await node.run(DAGState.empty(), DAGDeps(task=task, db=db))
    # `[channel`, not "staging": the field schema's own `doc` for
    # `environment` reads "production, staging or dev", so asserting on the
    # word passes and fails for reasons that have nothing to do with the room.
    # That mistake cost two attempts on this ticket already.
    assert len(asked) == 1 and "[channel" not in asked[0]

    # The operator now writes down what the room is, on the board's memory
    # form — a row, live on the next read, with no reload to forget.
    await db.memory_add(
        FridayState(channel_id="watched", agent="operator"),
        "test.apero chạy trên dev", kind="fact",
        origin=MemoryOrigin.ADMIN,
    )

    await node.run(DAGState.empty(), DAGDeps(task=await db.task(task.id), db=db))

    assert len(asked) == 2, "the new fact never reached a model"
    assert "[channel" in asked[-1], "the model was called without the room"
    assert "test.apero" in asked[-1], "the fact itself never arrived"


# --- ticket 08: the build respects a budget ---------------------------------


async def test_prepare_node_truncates_the_build_to_the_configured_budget(db):
    """The wiring end to end: `prepare_node(..., budget_tokens=...)` reaches
    `original_text_for` through `deps.db`, not a rebuilt query — the same
    seam `test_a_room_fact_reaches_the_extractor_and_settles_the_field`
    already proves for the room."""
    from friday.kernel.dag.prepare import prepare_node
    from tests.test_extraction import _install
    from tests.test_pool import _said

    seen: list[str] = []

    class _RecordsTheText(_StandsForAnExtractor):
        async def run(self, context, *, task_id=None, node=None):
            seen.append(context.transcript)
            return ApiIssueParams(summary="checkout 500"), None

    _install("devops.api_issue", _RecordsTheText())
    task = await _reported(db, text="@Lee " + ("API lỗi rồi. " * 40))
    await _said(db, "m2", "correlationId là abcdef01-2345-6789-abcd-ef0123456789", secs=3)
    node = prepare_node("devops.api_issue", ApiIssueParams, budget_tokens=20)

    await node.run(DAGState.empty(), DAGDeps(task=task, db=db))

    (text,) = seen
    assert "API lỗi rồi" not in text, "the oldest message should have been dropped"
    assert "abcdef01-2345-6789-abcd-ef0123456789" in text


async def test_an_unconfigured_budget_reaches_prepare_node_as_no_compaction(db):
    """`budget_tokens=None`, the default `prepare_node` and `build_simple_dag`
    both carry unless `config.yaml` sets one — the exact behaviour every
    install had before this ticket."""
    from friday.kernel.dag.prepare import prepare_node
    from tests.test_extraction import _install
    from tests.test_pool import _said

    seen: list[str] = []

    class _RecordsTheText(_StandsForAnExtractor):
        async def run(self, context, *, task_id=None, node=None):
            seen.append(context.transcript)
            return ApiIssueParams(summary="checkout 500"), None

    _install("devops.api_issue", _RecordsTheText())
    task = await _reported(db, text="@Lee " + ("API lỗi rồi. " * 40))
    await _said(db, "m2", "correlationId là abcdef01-2345-6789-abcd-ef0123456789", secs=3)
    node = prepare_node("devops.api_issue", ApiIssueParams)

    await node.run(DAGState.empty(), DAGDeps(task=task, db=db))

    (text,) = seen
    assert "API lỗi rồi" in text, "nothing should be dropped with no budget configured"


async def test_two_ineffective_compactions_stop_a_third_from_being_attempted(db):
    """D6: a single message larger than the budget is not something dropping
    older messages can fix. Two such passes and node 0 stops trying — the
    third pass reads the full text, exactly as an unset budget would."""
    from friday.kernel.dag.prepare import prepare_node
    from tests.test_extraction import _install

    class _AlwaysFillsSomething(_StandsForAnExtractor):
        async def run(self, context, *, task_id=None, node=None):
            return ApiIssueParams(summary="checkout 500"), None

    _install("devops.api_issue", _AlwaysFillsSomething())
    huge = "@Lee " + ("API lỗi rồi rất là dài. " * 200)
    task = await _reported(db, text=huge)
    node = prepare_node("devops.api_issue", ApiIssueParams, budget_tokens=5)

    await node.run(DAGState.empty(), DAGDeps(task=task, db=db))
    assert await db.compaction_ineffective_count(task.id) == 1
    assert await db.compaction_on_cooldown(task.id) is False

    await node.run(DAGState.empty(), DAGDeps(task=task, db=db))
    assert await db.compaction_ineffective_count(task.id) == 2
    assert await db.compaction_on_cooldown(task.id) is True

    # A third pass must not record a third ineffective compaction: once on
    # cooldown, the budget is not even checked.
    await node.run(DAGState.empty(), DAGDeps(task=task, db=db))
    assert await db.compaction_ineffective_count(task.id) == 2, (
        "a pass on cooldown must not check the budget at all"
    )


async def test_node_0_puts_the_artifact_back_rather_than_storing_a_retyping(db):
    """Board `read-it-the-way-the-operator-does`, ticket 18.

    The extractor names the artifact holding the request; node 0 writes the
    artifact's content into the task's parameters. What is stored has to be
    the reporter's own bytes — task 6 stored 676 characters of a
    678-character Bearer token, and a request that 401s on a signature nobody
    broke sends the operator down a path the reporter never went down.

    Driven through `prepare_node` rather than `resolve_artifacts` on purpose:
    the call that was missing is the wiring, not the function.
    """
    from dataclasses import replace as _replace

    from conftest import make_event
    from friday.kernel.dag.prepare import prepare_node
    from tests.test_extraction import _install
    from tests.test_pool import make_task

    curl = "curl -X POST /v1/pod/orders/init -H 'Authorization: Bearer eyJhbGci.zzz'"
    event = _replace(
        make_event(message_id="m1", text=f"lỗi rồi anh ơi\n```\n{curl}\n```"),
        code=(curl,),
    )
    task = await make_task(db)
    await db.record_message(event)
    await db.mark_triaged(event, task.id, decision={"type": "devops.api_issue"})
    (artifact,) = await db.artifacts_for_message("fake", "m1")

    _install("devops.api_issue", _CountingExtractor(ApiIssueParams(curl=artifact.id)))
    node = prepare_node("devops.api_issue", ApiIssueParams)

    await node.run(DAGState.empty(), DAGDeps(task=task, db=db))

    stored = await db.task(task.id)
    assert stored.params["curl"] == curl
