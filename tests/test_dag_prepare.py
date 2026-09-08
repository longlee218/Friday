"""The api_issue workflow — deterministic branching, no model.

The rule this encodes is the most frequent real action there is: a report
arrives without the fields needed to trace it, and the first move is to ask.

Ticket 33 moved that rule from `plan_api_issue` into the last node of the
`api_issue` graph. These tests follow it there: they exercise
`prepare`, the node every graph shares, with no agents and no tool servers —
exactly the state a fresh install is in.
"""

from __future__ import annotations

from friday.dag.engine import DAGDeps, DAGState
from friday.domain.models import AccessRequestParams, ApiIssueParams, DocQuestionParams
from types import SimpleNamespace

from friday.domain.actions import Ask, HandOver
from friday.dag.prepare import prepare


async def _decide(task_type, params, *, text=None):
    """The simple path, exactly as `build_simple_dag`'s node wires it —
    `prepare_node`'s `on_ready` is `plan_by_required_parameters`.

    There was a `plan()` doing this, and every test here called it. Production
    did not — the node calls these two and routes to a graph in between — so a
    convenience wrapper had become a second path that only tests took, which is
    how five tests came to hold an unreachable branch elsewhere in this file.
    """
    from friday.dag.prepare import plan_by_required_parameters, prepare

    params, problem = await prepare(task_type, params, text=text)
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
    _, problem = await prepare("api_issue", params(**kw))
    return problem


async def test_a_report_with_nothing_to_trace_on_asks_for_details():
    action = await gate()

    assert isinstance(action, Ask)
    assert "correlationId" in action.text or "curl" in action.text.lower()


async def test_a_correlation_id_is_enough_to_reach_the_graph():
    assert await gate(correlation_id=CID) is None


async def test_a_curl_is_enough_to_reach_the_graph():
    assert await gate(curl="curl https://x") is None


async def test_an_environment_alone_is_not_enough_to_trace():
    """You cannot find a request from the environment name."""
    assert isinstance(await gate(environment="production"), Ask)


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
    """A correlationId makes a request findable; its type cannot say that."""
    traceable = ApiIssueParams(
        summary="s",
        environment=None,
        correlation_id="abcdef01-2345-6789-abcd-ef0123456789",
        curl=None,
    )

    assert isinstance(await _decide("api_issue", traceable), HandOver)


async def test_a_malformed_correlation_id_is_caught_by_the_rule():
    """A value that does not look like a uuid is rejected before dispatch.

    Ticket 31 adds InSet/Matches rules to ApiIssueParams; ticket 30 wired
    validate into plan(). Together they catch what triage left through that
    the structural check did not: not 'field is missing', but 'field is
    wrong'."""
    bad = ApiIssueParams(summary="s", correlation_id="abc-123")

    action = await _decide("api_issue", bad)

    assert isinstance(action, Ask)
    assert "uuid" in action.text


# ---- ask_clarification: code stays the floor (D12) --------------------------


async def _prepare_with_clarify(params_obj, clarify, *, extracted=None, monkeypatch):
    """`prepare()` with `extract()` stood in for, so the ordering between
    code's own floor and a model's `Clarify` can be tested without a real
    extractor or model."""
    import friday.dag.prepare as wf

    async def stub_extract(task_type, text, *, task_id=None, node=None):
        return extracted, clarify

    monkeypatch.setattr(wf, "_extract", stub_extract)
    return await wf.prepare("api_issue", params_obj, text="irrelevant")


async def test_code_floor_wins_over_a_clarify_that_names_a_different_field(monkeypatch):
    """The extractor asked about `environment`, but `correlation_id` is
    malformed — code's own rule is what the reporter is challenged with,
    because a value the rules reject cannot be waved through by the model
    having asked about something else instead."""
    from friday.extraction import Clarify

    bad = params(correlation_id="not-a-uuid")
    clarify = Clarify(fields=("environment",), because="no server named")

    _, action = await _prepare_with_clarify(bad, clarify, monkeypatch=monkeypatch)

    assert isinstance(action, Ask)
    assert "uuid" in action.text
    assert "server" not in action.text, "the model's own wording must not leak in here"


async def test_a_clarify_for_an_already_filled_field_is_not_honoured(monkeypatch):
    """The model asked about `correlation_id`, but it is already there — from
    the reporter, or from this same extraction run. Asking again for
    something already answered is not a question this exists to ask."""
    from friday.extraction import Clarify

    complete = params(correlation_id=CID)
    clarify = Clarify(fields=("correlation_id",), because="not sure")

    _, action = await _prepare_with_clarify(complete, clarify, monkeypatch=monkeypatch)

    assert action is None, "nothing left to ask about once the field is filled"


async def test_a_clarify_becomes_an_ask_once_code_has_nothing_to_say(monkeypatch):
    """A report with a curl is traceable — code's own rules find nothing
    wrong — but the extractor read something worth asking about anyway.
    That is the case `ask_clarification` exists for."""
    from friday.extraction import Clarify

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
        "api_issue",
        ApiIssueParams(
            summary="s", correlation_id="abcdef01-2345-6789-abcd-ef0123456789"
        ),
    )

    assert isinstance(action, HandOver)


# --- a later extraction fills blanks; it does not revise what it said --------


def test_a_second_extraction_does_not_reword_the_first():
    """This runs again on every follow-up, and a model asked the same question
    twice does not give the same answer. Letting the second run rewrite the
    first cost nineteen direct messages about one report, each carrying a
    differently worded summary — a reworded value is a *changed* value, so the
    graph discarded its work and the operator was told again."""
    from friday.dag.prepare import _fill

    filled = _fill(
        ApiIssueParams(summary="checkout is 500ing", environment="production"),
        ApiIssueParams(summary="User reports the API is failing", environment="prod"),
    )

    assert filled.summary == "checkout is 500ing"
    assert filled.environment == "production"


def test_a_later_extraction_fills_what_is_still_blank():
    """Which is exactly what a follow-up supplying the correlationId is, and
    the only way it reaches the task now that triage does not lift it out."""
    from friday.dag.prepare import _fill

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
    from friday.dag.prepare import _fill

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
    import friday.dag.prepare as wf
    from friday.agent.harness import Refused
    from friday.domain.actions import HandOver
    from friday.domain.models import ApiIssueParams

    async def refused(task_type, text, *, task_id=None, node=None):
        raise Refused("api_issue_extractor has spent 999 of its 10 tokens today")

    monkeypatch.setattr(wf, "_extract", refused)

    _, action = await wf.prepare(
        "api_issue",
        ApiIssueParams(summary="checkout 500"),
        text="prod broke, correlationId abcdef01-2345-6789-abcd-ef0123456789",
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
        make_event(message_id="m1"), task.id, decision={"type": "api_issue"}
    )
    return task


class _CountingExtractor:
    """Stands in for the model. Returns the same answer every time, which is
    the point: the same facts must not be paid for twice."""

    def __init__(self, params, clarify=None):
        self.params, self.clarify, self.texts = params, clarify, []

    async def run(self, text, *, task_id=None, node=None):
        self.texts.append(text)
        return self.params, self.clarify


async def test_node_0_pays_once_when_nothing_has_changed(db):
    """One task in the recorded data has two extractor calls of 1,790 input
    tokens whose prompts are byte-identical — same sha256, seven and a half
    hours apart. Node 0 is excluded from the checkpoint and re-executes on
    every pass, which is correct, because it must see a message that arrived
    since the last one. What it must not do is call a model when nothing did.
    """
    from friday.dag.prepare import prepare_node
    from tests.test_extraction import _install

    extractor = _CountingExtractor(ApiIssueParams(summary="checkout 500"))
    _install("api_issue", extractor)
    task = await _reported(db)
    node = prepare_node("api_issue", ApiIssueParams)

    first = await node.run(DAGState.empty(), DAGDeps(task=task, db=db))
    second = await node.run(
        DAGState.empty(), DAGDeps(task=await db.task(task.id), db=db)
    )

    assert len(extractor.texts) == 1, (
        f"extracted {len(extractor.texts)} times for one unchanged task"
    )
    assert second == first, "the second pass reached a different conclusion"


async def test_a_new_message_is_paid_for(db):
    """The reason node 0 re-runs at all. A reporter who sends the curl three
    seconds later must be read, so a changed transcript has to reach the
    model even though the parameters have not moved."""
    from friday.dag.prepare import prepare_node
    from tests.test_extraction import _install
    from tests.test_pool import _said

    extractor = _CountingExtractor(ApiIssueParams(summary="checkout 500"))
    _install("api_issue", extractor)
    task = await _reported(db)
    node = prepare_node("api_issue", ApiIssueParams)

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
    from friday.dag.prepare import prepare_node
    from friday.extraction import Clarify
    from tests.test_extraction import _install

    # A correlationId that validates, so `_problems` is empty and the
    # extractor's own question is the only thing that can produce an `Ask`.
    # Without it the `_traceable` rule fires first and both passes return the
    # same code-written question — which is how the first version of this test
    # passed with the replay deleted.
    extractor = _CountingExtractor(
        ApiIssueParams(
            summary="service down",
            correlation_id="3f7a1e22-8b44-4c31-9d0e-77a2c6b51e90",
        ),
        clarify=Clarify(("environment",), "the URL says test, which is not an env"),
    )
    _install("api_issue", extractor)
    task = await _reported(db)
    node = prepare_node("api_issue", ApiIssueParams)

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


def test_the_fingerprint_covers_the_field_schema_not_only_the_text():
    """The extractor's prompt is the field schema and the text. Fingerprinting
    the text alone would mean a task already marked is never read again under a
    reworded field — the schema changed, the prompt changed, and nothing said
    so."""
    from friday.dag.prepare import _fingerprint

    assert _fingerprint(ApiIssueParams, "API lỗi") != _fingerprint(
        AccessRequestParams, "API lỗi"
    )
    assert _fingerprint(ApiIssueParams, "API lỗi") == _fingerprint(
        ApiIssueParams, "API lỗi"
    ), "the same schema and the same text must be the same fingerprint"


async def test_a_parameter_change_the_extractor_cannot_see_is_not_paid_for(db):
    """**A deliberate deviation from ticket 04's own criterion**, which asked
    for the task's parameters to count toward "has anything changed".

    They cannot. The extractor's input is `build_input(text, params_cls)` — the
    field schema and what the reporter wrote. Parameters never reach it, so a
    parameter that moved is not a reason to pay for the same answer again. The
    fill is replayed from the mark, so the outcome is the same either way."""
    from friday.dag.prepare import prepare_node
    from tests.test_extraction import _install

    extractor = _CountingExtractor(ApiIssueParams(summary="checkout 500"))
    _install("api_issue", extractor)
    task = await _reported(db)
    node = prepare_node("api_issue", ApiIssueParams)

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
    from friday.dag.prepare import prepare_node
    from tests.test_extraction import _install

    class _Failing:
        def __init__(self):
            self.calls = 0

        async def run(self, text, *, task_id=None, node=None):
            self.calls += 1
            return None, None

    extractor = _Failing()
    _install("api_issue", extractor)
    task = await _reported(db)
    node = prepare_node("api_issue", ApiIssueParams)

    await node.run(DAGState.empty(), DAGDeps(task=task, db=db))
    await node.run(DAGState.empty(), DAGDeps(task=await db.task(task.id), db=db))

    assert extractor.calls == 2, "a failed call was remembered as an answer"
    assert await db.extraction_mark(task.id) is None, "marked an empty extraction"
