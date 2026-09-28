"""Ticket 00 — the vertical slice, node by node and end to end.

The property the whole board turns on: **a complete api_issue report is
investigated, not handed back.** Before this graph existed, node 0 filled the
parameters in, found nothing to ask about and said "everything needed is
here, and there is no investigation past this point" — which was true, and
is what task 6 met on 2026-09-20.

Every other test here guards one of the two halves that make an
investigation trustworthy: it stops rather than guessing when a row is
missing, and it says out loud what it did not check.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from plugins.backend.graph import build_backend_dag, build_log_sources
from plugins.backend.config import BackendConfig, load_backend_config
from plugins.backend.config import DEFAULT_CONTAINER_ROOTS
from plugins.backend.graph.diagnose import (
    Diagnosis,
    diagnose_node,
    unresolved_refs,
)
from plugins.backend.graph.report import render, report_node
from plugins.backend.sources.code import repo_file


class _StubCaps:
    """A stand-in for the composition root's boot caps, for building the backend
    DAG in a test without a live process (ticket 14)."""

    def __init__(self, *, diagnose_harness=None, budget_tokens=None, diagnose_agent=None):
        self.config = SimpleNamespace(
            agent=lambda declaration: diagnose_agent,
            context=SimpleNamespace(extraction_budget_tokens=budget_tokens),
        )
        self.servers = {}
        self.sender = "discord_user"
        self.approver = "discord_bot"
        self._harness = diagnose_harness

    def make_harness(self, *, agent, instructions, answers=None, tools=None,
                     ends_with=None):
        return self._harness

    async def intake(self, *args, **kwargs):
        return await core_intake(*args, **kwargs)


def _dag(*, diagnose_harness=None, reports_dir=None, budget_tokens=None, diagnose_agent=None):
    """The `backend.trace_problem` DAG, built through the plugin's own builder."""
    api = SimpleNamespace(
        caps=_StubCaps(
            diagnose_harness=diagnose_harness,
            budget_tokens=budget_tokens,
            diagnose_agent=diagnose_agent,
        ),
        config=BackendConfig(reports_dir=str(reports_dir) if reports_dir else "./data/reports"),
    )
    return build_backend_dag(api)

from plugins.backend.graph.deps import ApiIssueDeps
from plugins.backend.graph.intake import intake_of
from friday.kernel.spine.intake import intake as core_intake
from friday.sdk.workflow import DAGState, status_of
from friday.sdk.actions import Ask, HandOver, Reply
from friday.kernel.domain.conversation import ConversationId
from friday.sdk.memory import MemoryOrigin
from friday.kernel.domain.state import FridayState
from plugins.backend.params import ApiIssueParams

CURL = (
    'curl -X POST -H "Content-Type: application/json" '
    '--data \'{"items":[]}\' '
    '"https://api-reelme-v2.dev.aperogroup.ai/v1/pod/orders/init"'
)


# --- fixtures ---------------------------------------------------------------


REPORTED_AT = datetime(2026, 9, 20, 4, 40, 46, tzinfo=timezone.utc)


def deps_for(db, *, task_id: int = 1, extra: dict | None = None) -> ApiIssueDeps:
    """The typed `Deps` an `api_issue` node is run with (ticket 13). Call sites
    still pass the handles as an `extra` dict; this maps them onto the typed
    fields, so a node's `deps.log_sources`/`deps.sender` see the same values."""
    extra = extra or {}
    return ApiIssueDeps(
        task=SimpleNamespace(
            id=task_id,
            conversation=ConversationId("fake", "watched"),
            params={},
            created_at=REPORTED_AT,
        ),
        db=db,
        sender=extra.get("sender", ""),
        approver=extra.get("approver", ""),
        log_sources=extra.get("log_sources", {}),
        release_source=extra.get("release_source"),
    )


def prepared(**params) -> DAGState:
    return DAGState.empty().with_result(
        "prepare",
        ApiIssueParams(**{"summary": "500 khi init đơn", "curl": CURL, **params}),
    )


async def write_environment_rows(db, channel_id: str = "watched"):
    """What a domain suffix means. The convention as two rows, longest-suffix
    wins — which is also how an exception is written (2026-09-21, amending
    D1)."""
    state = FridayState(channel_id=channel_id, agent="admin")
    for suffix, env in (("aperogroup.ai", "production"), ("dev.aperogroup.ai", "dev")):
        await db.memory_add(
            state, f"{suffix} is {env}", kind="backend.environment",
            origin=MemoryOrigin.ADMIN, data={"suffix": suffix, "env": env},
        )


async def write_rows(db, *, env: str = "dev", repo: str | None = None):
    """The rows ticket 00 says are typed by hand: the environment table, the
    project, the service, and the route into it.

    **In that order, and ticket 19 is why.** A `route` names a `service` and
    a `service` names a `project`; the store refuses a row naming one that
    does not exist yet, so the order a person would naturally type them in is
    the order this has to build them.
    """
    state = FridayState(channel_id="watched", agent="admin")
    await write_environment_rows(db)
    domain = (
        "api-reelme-v2.dev.aperogroup.ai" if env == "dev"
        else "api-reelme-v2.aperogroup.ai"
    )
    await db.memory_add(
        state, "the ReelMe repository", kind="backend.project",
        origin=MemoryOrigin.ADMIN,
        data={
            "name": "reelme", "repo_path": repo or "/nowhere",
            "default_branch": "main", "stack": "NestJS",
        },
    )
    await db.memory_add(
        state, "the ReelMe v2 backend", kind="backend.service",
        origin=MemoryOrigin.ADMIN,
        data={
            "name": "backend-reelme-v2",
            "project": "reelme",
            "prod": {"cluster": "vultr-ailab", "namespace": "sw", "app": "backend-reelme-v2"},
            "dev": {"kube_context": "dev", "namespace": "dev", "pod_pattern": "backend-reelme-v2"},
        },
    )
    await db.memory_add(
        state, "ReelMe v2 on dev", kind="backend.route", origin=MemoryOrigin.ADMIN,
        data={"domain": domain, "env": env, "service": "backend-reelme-v2"},
    )


@dataclass


class FakeSource:
    """A log source that answers from a script and records what it was asked."""

    answers: list[list[str]]
    name: str = "kubectl"
    asked: list[tuple] = None  # type: ignore[assignment]

    def __post_init__(self):
        self.asked = []

    #: What the pod's oldest and newest lines are, when the fake is standing
    #: in for a back end that can say. `None` is one that cannot.
    oldest: object = None
    newest: object = None
    truncated: bool = False

    async def lines(self, placement, *, since, until, limit: int, needle: str = ""):
        """Answers from the script, and **honours `needle` the way a real
        back end does** — by returning only the lines that carry it.

        Faithful on purpose: the node now reads twice per window, once for
        the window and once narrowed to the request, and a fake that ignored
        the narrowing would let the node pass a test it fails against Loki.
        The script advances per *window*, not per call, because the two reads
        of one window are two views of the same log.

        `truncated` is the window read's, never the narrowed one's: a cap is
        hit by asking for everything in a busy window, which is the case the
        narrowing exists to avoid.
        """
        from friday.sdk.sources import Lines

        self.asked.append((since, until, needle))
        answer = self.answers[min(len(self.spans) - 1, len(self.answers) - 1)]
        if needle:
            return Lines(
                tuple(l for l in answer if needle in l), self.oldest, self.newest
            )
        return Lines(
            tuple(answer), self.oldest, self.newest, truncated=self.truncated
        )

    @property
    def spans(self) -> list[tuple]:
        """The distinct windows asked for, in order.

        One entry per window rather than per read — the narrowed read is not
        a second window, and counting it as one would make every widening
        assertion in this file read as two.

        **What the script is keyed on**, deliberately separate from
        `windows` below. Scripting off a *formatting* helper means changing
        how a span is rendered silently re-scripts the fake, and a fake that
        answers differently for a reason nobody wrote down is worse than no
        fake.
        """
        seen: list[tuple] = []
        for since, until, _ in self.asked:
            if not seen or seen[-1] != (since, until):
                seen.append((since, until))
        return seen

    @property
    def windows(self) -> list[str]:
        """The same windows as "<hours>h" of lookback, for a readable
        assertion. Rendering only — nothing depends on its shape."""
        return [
            f"{round((until - since).total_seconds() / 3600, 2)}h"
            for since, until in self.spans
        ]


# --- resolve.py's surviving helpers (node 1 itself is gone, ticket 6) -------


async def test_one_row_for_one_host_beats_the_convention_it_breaks(db):
    """The 2026-09-18 survey found `api-mobile-spec-reviewer.aperogroup.ai`
    served from `dev` with no `.dev` in it. Longest suffix wins, so the
    exception is one more row rather than a branch."""
    from plugins.backend.resolve import environment_of

    rows = [
        SimpleNamespace(suffix="aperogroup.ai", env="production"),
        SimpleNamespace(suffix="dev.aperogroup.ai", env="dev"),
        SimpleNamespace(suffix="api-mobile-spec-reviewer.aperogroup.ai", env="dev"),
    ]

    assert environment_of("api-reelme-v2.aperogroup.ai", rows) == "production"
    assert environment_of("api-reelme-v2.dev.aperogroup.ai", rows) == "dev"
    assert environment_of("api-mobile-spec-reviewer.aperogroup.ai", rows) == "dev"
    assert environment_of("api.stripe.com", rows) == "external"


# --- find_request_log -------------------------------------------------------


def test_the_kubectl_window_is_clipped_by_the_runtimes_own_stamps():
    """`--tail` counts from the newest line and there is no `--until`, so a
    window that opened sixteen hours ago came back as the newest 400 lines of
    today — and the node reported them as the reporter's. `--timestamps` is
    what makes the upper bound possible, and it is the container runtime's
    stamp rather than the line's own field, because dev is JSON today and a
    Python traceback tomorrow."""
    from plugins.backend.sources.logs import _within

    since = datetime(2026, 9, 20, 4, 10, tzinfo=timezone.utc)
    until = datetime(2026, 9, 20, 4, 45, tzinfo=timezone.utc)

    found = _within(
        [
            'Defaulted container "backend-reelme-v2" out of: …',
            "2026-09-21T07:38:50.548Z hôm nay, ngoài cửa sổ",
            "2026-09-20T04:40:00.000Z đúng request của reporter",
        ],
        since=since, until=until,
    )

    assert found.lines == (
        'Defaulted container "backend-reelme-v2" out of: …',
        "đúng request của reporter",
    ), "kubectl's own warning is kept; the stamp is taken back off"
    assert found.oldest == datetime(2026, 9, 20, 4, 40, tzinfo=timezone.utc)


# --- read_failing_code ------------------------------------------------------


def test_a_frame_is_mapped_into_the_clone(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "orders.ts").write_text("a\nb\nc\n")

    found = repo_file("/app/src/orders.ts", str(tmp_path), container_roots=DEFAULT_CONTAINER_ROOTS)

    assert found == (tmp_path / "src" / "orders.ts").resolve()


def test_a_frame_that_climbs_out_of_the_clone_is_never_opened(tmp_path):
    """A frame arrives from a log line, and a log line carries whatever
    somebody got the service to print (finding D).

    The file it climbs to has to **exist**, or this passes for the wrong
    reason: a traversal that lands on nothing is refused by `is_file()`
    whether or not anything checks the root. Deleting the root check left
    this green until the secret below was a real file.
    """
    repo = tmp_path / "clone"
    (repo / "src").mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("not yours")

    assert repo_file("/app/../secret.txt", str(repo), container_roots=DEFAULT_CONTAINER_ROOTS) is None
    assert repo_file("/etc/passwd", str(repo), container_roots=DEFAULT_CONTAINER_ROOTS) is None
    assert secret.is_file(), "the escape target was real"


# --- diagnose ---------------------------------------------------------------


def test_a_pointer_to_a_line_nobody_showed_it_does_not_resolve():
    """Pointers, not quotes (spec, measured): the model names a line and code
    puts the text back, so the check is a lookup rather than a decision about
    how much rewrapping to forgive."""
    diagnosis = Diagnosis(
        cause="x", confidence="likely", conclusive=True, refs=["L1", "L99"],
    )

    assert unresolved_refs(diagnosis, {"L1": "ERROR ERR19"}) == ["L99"]


def test_a_pointer_is_read_however_the_model_spelled_it():
    diagnosis = Diagnosis(
        cause="x", confidence="likely", conclusive=True,
        refs=["L12", "l12", "L12:", "line 12"],
    )

    assert unresolved_refs(diagnosis, {"L12": "ERROR"}) == []


async def test_an_ungrounded_diagnosis_is_not_reported(db):
    """The failure this board exists to avoid: a cause built on something the
    model supplied itself, asserted in the operator's name."""
    result = await diagnose_node(
        make_harness=_answered_after_reading(
            cause="the database is down", confidence="certain",
            conclusive=True, refs=["L99"],
        ),
        agent="backend.diagnose",
    ).run(_reads_state(), _reading_deps(db))

    assert status_of(result) == "empty"
    assert "L99" in result["reason"]


async def test_a_conclusive_answer_that_points_at_nothing_is_not_reported(db):
    """Otherwise the gate is optional: an answer with no pointers has nothing
    to refuse, and "conclusive" is exactly the claim that needs one."""
    result = await diagnose_node(
        make_harness=_answered_after_reading(
            cause="nó hỏng", confidence="certain", conclusive=True, refs=[],
            # Filled, so this reaches the refs gate rather than the
            # alternatives one: the two refuse for different reasons and
            # this test is about pointing at nothing.
            alternatives_rejected=[
                {"hypothesis": "mạng chập", "why": "không có timeout nào"}
            ],
        ),
        agent="backend.diagnose",
    ).run(_reads_state(), _reading_deps(db))

    assert status_of(result) == "empty"
    assert "pointed at" in result["reason"]


async def test_no_diagnose_agent_skips_rather_than_failing(db):
    result = await diagnose_node().run(prepared(), deps_for(db))

    assert status_of(result) == "skipped"


# --- report -----------------------------------------------------------------


async def test_the_report_is_written_and_the_reporter_is_offered_the_cause(db, tmp_path):
    state = (
        _intake_state(env="dev", service="backend-reelme-v2", pod_pattern="p")
        .with_result("diagnose", {
            "status": "ok", "reason": "",
            "diagnosis": {
                "cause": "ERR19", "confidence": "likely", "conclusive": False,
                "refs": ["L1"], "next_checks": ["hỏi reporter"],
            },
            "quotes": ["ERROR ERR19"],
            "not_checked": ["read at HEAD"],
        })
    )

    result = await report_node(reports_dir=tmp_path).run(state, deps_for(db))

    (written,) = list(tmp_path.glob("*.md"))
    # D10: "a report file at `data/reports/<task_id>.md`" — one per task, so
    # a re-run after the reporter answers replaces it rather than leaving two
    # reports that disagree.
    assert written.name == "1.md"
    assert isinstance(result, Reply)
    assert "ERR19" in result.text
    assert "ERR19" in written.read_text()
    assert "read at HEAD" in written.read_text()


def test_the_report_no_longer_renders_the_dossier_sections():
    """`render` used to read `find_request_log`/`read_failing_code` for a log
    source line, a counted error-code table and the raw lines each fed the
    model — both nodes are gone (ticket 05), and a checkpoint written before
    this change can still carry their keys. `render` must not resurrect them;
    `not_checked` now comes off the diagnose node's own envelope alone."""
    state = (
        DAGState.empty()
        .with_result(
            "find_request_log",
            {"status": "ok", "reason": "", "dossier": "ERROR ERR19",
             "source": "kubectl", "kept": 1, "total": 90,
             "histogram": [["ERR19", 90]]},
        )
        .with_result(
            "read_failing_code",
            {"status": "ok", "reason": "", "code": "throw new Error()"},
        )
        .with_result("diagnose", {
            "status": "empty", "reason": "no diagnose agent is configured",
            "not_checked": ["the log does not reach back to the report"],
        })
    )

    text = render(state, task_id=6, at=datetime(2026, 9, 20, tzinfo=timezone.utc))

    assert "log source" not in text
    assert "ERROR ERR19" not in text
    assert "throw new Error" not in text
    assert "the log does not reach back to the report" in text
    assert "no diagnose agent is configured" in text


# --- the graph --------------------------------------------------------------


# --- three outputs, one of them approved (ticket 06) ------------------------


async def test_the_reporter_is_told_it_is_being_worked_on(db):
    """Seconds of log reading, a clone and a model call. A reporter told
    nothing in that time does not know anything is happening, and what people
    do about silence is ask again."""
    from conftest import make_event

    from plugins.backend.graph.acknowledge import ack_text, acknowledge_node
    from friday.kernel.outbox import DEFAULT_SENDER, Kind

    await db.record_message(make_event(message_id="m1"))
    state = _intake_state(service="backend-reelme-v2")
    result = await acknowledge_node().run(
        state, deps_for(db, extra={"sender": DEFAULT_SENDER})
    )

    assert status_of(result) == "ok"
    (row,) = await db.outbound()
    assert row.kind == Kind.ACKNOWLEDGED
    assert row.text == ack_text(intake_of(state["intake"]).domain)
    assert row.sender == DEFAULT_SENDER, "the reporter's channel, not a DM"
    # Hung under the message it answers, which is what keeps a busy channel
    # readable. A real mention is recorded first, or both sides of this are
    # `None` and it asserts nothing — which is what it did until review
    # pointed out that deleting the `reply_to` changed no test.
    assert row.reply_to == "m1"


async def test_the_acknowledgement_does_not_wait_for_approval(db):
    """The operator's call, 2026-09-22. It promises no finding, quotes no
    log line and names no fault — and one that waits for a person arrives
    after the reply it was meant to precede."""
    from plugins.backend.graph.acknowledge import acknowledge_node
    from friday.kernel.outbox import DEFAULT_SENDER, Kind

    await acknowledge_node().run(
        _intake_state(), deps_for(db, extra={"sender": DEFAULT_SENDER})
    )

    assert not Kind.ACKNOWLEDGED.needs_approval
    assert [r.kind for r in await db.sendable_outbound()] == [Kind.ACKNOWLEDGED]


async def test_a_resumed_graph_does_not_acknowledge_twice(db):
    """A graph re-runs from its checkpoint after the reporter answers a
    question. A second "đang xử lý" three minutes after the first reads as a
    stuck robot."""
    from plugins.backend.graph.acknowledge import acknowledge_node
    from friday.kernel.outbox import DEFAULT_SENDER

    deps = deps_for(db, extra={"sender": DEFAULT_SENDER})
    state = _intake_state()
    await acknowledge_node().run(state, deps)
    again = await acknowledge_node().run(state, deps)

    assert status_of(again) == "skipped"
    assert len(await db.outbound()) == 1


def test_nobody_is_acknowledged_before_the_placement_is_known():
    """`intake` always runs — it makes no model call and hands over on
    nothing — so nobody today reaches this edge with a hand-over. The gate
    stays anyway: `acknowledge` must never fire from a node whose result was
    not `ok`, whatever kind of result that turns out to be.

    Asserts *which node* `intake` leads to, not merely that a hand-over
    stops the walk — the first version of this test asserted the latter,
    which is a different test that already existed, and stayed green with
    `acknowledge` moved ahead of `intake`.
    """
    dag = _dag()
    ok = DAGState.empty().with_result("intake", {"status": "ok", "reason": ""})

    assert dag.next_after("intake", ok) == "acknowledge"
    assert dag.next_after(
        "intake", DAGState.empty().with_result("intake", HandOver("not ours"))
    ) is None
    assert [n.name for n in dag.nodes].index("acknowledge") > \
        [n.name for n in dag.nodes].index("intake")


def test_ack_text_names_the_service_or_stays_generic():
    """Names the service when Intake resolved one; otherwise stays generic —
    never 'log của external' (external is not a place a reporter knows, and the
    loop reads code/docs, not only logs). Operator's fog-item call, 2026-09-27."""
    from plugins.backend.placement import Placement

    from plugins.backend.graph.acknowledge import ack_text

    named = ack_text(Placement(env="production", service="backend-reelme-v2"))
    assert "backend-reelme-v2" in named and "log của backend-reelme-v2" in named

    vague = ack_text(Placement(env="external", service="", candidates=("a", "b")))
    assert "external" not in vague and "log của" not in vague
    assert "xem lại vụ này" in vague


def test_the_reads_input_names_the_stack_when_known():
    """The stack (e.g. NestJS) is a hint the diagnose prompt gives the model so
    it reads a trace in that framework's idiom — restored on the unified
    Placement (2026-09-27). Absent when the placement carries no stack."""
    from plugins.backend.placement import Placement

    from plugins.backend.graph.prompt import build_reads_input

    with_stack = build_reads_input(
        report="loi 500",
        placement=Placement(env="dev", service="s", stack="NestJS"),
        not_checked=(),
    )
    assert "stack: NestJS" in with_stack

    without = build_reads_input(
        report="loi 500", placement=Placement(env="dev", service="s"), not_checked=()
    )
    assert "stack:" not in without


async def test_a_run_with_no_sender_investigates_anyway(db):
    """Nothing here is worth failing an investigation over — and a run that
    says why it stayed quiet beats one that is quiet about being quiet."""
    from plugins.backend.graph.acknowledge import acknowledge_node

    result = await acknowledge_node().run(DAGState.empty(), deps_for(db))

    assert status_of(result) == "skipped"
    assert "sender" in result["reason"]
    assert _dag().next_after(
        "acknowledge", DAGState.empty().with_result("acknowledge", result)
    ) == "diagnose"


def _diagnosed() -> DAGState:
    """A state that reached a cause, which is what the last two outputs need."""
    return DAGState.empty().with_result("diagnose", {
        "status": "ok", "reason": "",
        "diagnosis": {
            "cause": "ERR19 ở orders.init", "confidence": "likely",
            "conclusive": False, "refs": ["L1"], "next_checks": [],
        },
        "quotes": [], "not_checked": [],
    })


async def test_the_operator_is_told_where_the_whole_report_is(db, tmp_path):
    """The approval card carries what the *reporter* would see. This is the
    reading done before deciding whether they should see it."""
    from friday.kernel.outbox import DEFAULT_APPROVER, DEFAULT_SENDER, Kind

    state = _diagnosed()

    await report_node(reports_dir=tmp_path).run(
        state,
        deps_for(db, extra={"sender": DEFAULT_SENDER, "approver": DEFAULT_APPROVER}),
    )

    (row,) = [r for r in await db.outbound() if r.kind == Kind.FINDING]
    assert "1.md" in row.text
    assert "ERR19" in row.text
    # **Who reads it, not just what it says.** `sender` posts into the
    # reporter's channel as the watched account; `approver` DMs the operator.
    # Queued as `sender` — which this did until review caught it — the cause
    # and an absolute path on the operator's machine would be published
    # unapproved, ahead of the card asking whether to publish anything.
    assert row.sender == DEFAULT_APPROVER


async def test_with_nothing_concluded_the_reporter_is_offered_nothing(db, tmp_path):
    """A brief with no cause costs the operator an approval and tells the
    reporter that what they asked about is still unanswered — which the
    silence already said."""
    from friday.kernel.outbox import DEFAULT_APPROVER, Kind

    state = prepared().with_result(
        "diagnose", {"status": "skipped", "reason": "no diagnose agent"}
    )

    result = await report_node(reports_dir=tmp_path).run(
        state, deps_for(db, extra={"approver": DEFAULT_APPROVER})
    )

    assert isinstance(result, HandOver)
    assert [r.kind for r in await db.outbound()] == [Kind.FINDING]


def test_the_brief_carries_the_cause_and_not_the_evidence():
    """They asked what was wrong with their request. A local filesystem path
    says more about this machine than they need, and `next_checks` is what
    *this* investigation would do next — to a reporter it reads as a list of
    things they have been asked to do."""
    from plugins.backend.graph.report import brief

    said = brief(Diagnosis(
        cause="categoryId rỗng", confidence="certain", conclusive=True,
        refs=["L1"], next_checks=["hỏi BE về mapping"],
    ))

    assert said == "categoryId rỗng"


def test_a_brief_that_is_not_conclusive_says_so_in_words():
    from plugins.backend.graph.report import brief

    said = brief(Diagnosis(
        cause="có thể do cache", confidence="likely", conclusive=False, refs=["L1"],
    ))

    assert "chưa kết luận" in said


def test_the_pool_and_the_graph_send_as_the_same_two_identities():
    """The pool has always defaulted to them and the graph now queues rows of
    its own. Two defaults spelled separately are two answers to one question,
    and the day they disagree the graph's rows go out as somebody else — or
    to somebody else, which is worse.

    Compares the *values*, not the source text: the first version asserted
    the literal was absent from `pool.py`, which stayed green when the
    default was changed to a different string.
    """
    import inspect

    from friday.kernel.outbox import DEFAULT_APPROVER, DEFAULT_SENDER
    from friday.kernel.pool.pool import Pool

    taken = inspect.signature(Pool.__init__).parameters

    assert taken["sender"].default == DEFAULT_SENDER
    assert taken["approver"].default == DEFAULT_APPROVER


def test_api_issue_is_the_one_graph_with_an_investigation_past_node_zero():
    dag = _dag()

    assert [n.name for n in dag.nodes] == [
        "intake", "acknowledge", "diagnose", "report",
    ]


def test_nothing_past_intake_runs_when_intake_hands_over():
    """A node reading an earlier node's hand-over as if it were a result is
    the wiring mistake `DAGState` raises on, and the fix is in the edges."""
    dag = _dag()
    state = DAGState.empty().with_result("intake", HandOver("not ours"))

    assert dag.next_after("intake", state) is None


def test_a_blank_setting_means_not_configured_rather_than_the_word_none():
    """`ssh_host:` with nothing after it parses as `None`, and `str(None)` is
    a truthy `"None"` — which built a source pointing at a host called None
    and turned every dev task into a failed subprocess."""
    
    assert load_backend_config({"ssh_host": None}).ssh_host == ""
    assert build_log_sources(
        load_backend_config({"ssh_host": None}), {}
    ) == {}


def test_log_sources_are_only_built_for_what_is_configured():
    
    none = build_log_sources(BackendConfig(), {})
    dev = build_log_sources(
        BackendConfig(ssh_host="dev"), {}
    )
    prod = build_log_sources(
        BackendConfig(loki_server="backend"), {"backend": object()},
    )

    assert none == {}
    assert set(dev) == {"kubectl"}
    assert set(prod) == {"loki"}


async def test_a_complete_report_is_investigated_rather_than_handed_back(db, workflows):
    """The behaviour task 6 met, and the reason this board exists.

    Node 0 used to find nothing to ask about — a curl satisfies `_traceable`
    — and the answer was "everything needed is here, and there is no
    investigation past this point". `Intake` (ticket 06) makes no such call
    at all: it never hands over, so every report reaches `Diagnose`. With no
    `backend.diagnose` agent configured (every test that does not set one up),
    that node skips and `Report` hands the case to the operator rather than
    asserting a cause it has none for.
    """
    from friday.kernel.pool.pool import Pool

    await write_environment_rows(db)
    task_id = await _task_with_text(db, f"loi 500 tren backend-reelme-v2\n{CURL}")

    await Pool(db=db, auto_ask=True).run_once()

    pauses = await db.pauses_for([task_id])
    (question,) = pauses.values()
    assert "no investigation past this point" not in question
    assert "no diagnose agent is configured" in question


async def test_the_whole_line_runs_from_a_curl_to_a_report(db, tmp_path):
    """Every node of the slice, in one pass, with the rows and a source in
    place — the shape ticket 00's five cases are run through. The reads loop
    is the only diagnose mode now: `Diagnose` reads the log itself rather
    than being handed a dossier."""
    from replay_case import _run_on_adapter

    await write_rows(db, repo=str(tmp_path))
    task_id = await _task_with_text(
        db, "backend-reelme-v2 dang tra 500\n" f"```\n{CURL}\n```", code=(CURL,),
    )
    task = await db.task(task_id)

    class Honest:
        last_error = None

        def __init__(self, tools):
            self._tools = {t.fn.__name__: t.fn for t in tools}

        async def run_structured(self, prompt, **kw):
            assert "Nothing has been read for you" in prompt
            read = await self._tools["read_log"](needle="ERR19")
            assert "ERR19" in read
            return Diagnosis(
                cause="ERR19 ở orders.init", confidence="likely",
                conclusive=False, refs=["L1"],
            )

    class HonestCaps(_StubCaps):
        def make_harness(self, *, agent, instructions, answers=None,
                         tools=None, ends_with=None):
            return None if tools is None else Honest(tools)

    reports_dir = tmp_path / "reports"
    source = FakeSource(answers=[["ERROR ERR19 POST /v1/pod/orders/init 500"]])
    api = SimpleNamespace(
        caps=HonestCaps(),
        config=BackendConfig(reports_dir=str(reports_dir)),
    )
    dag = build_backend_dag(api)
    final, runs, _ = await _run_on_adapter(
        dag,
        deps=ApiIssueDeps(
            task=task, db=db, sender="", approver="",
            log_sources={"kubectl": source},
        ),
        seed={},
        system_db=tmp_path / "sys.db",
        wfid="test-whole-line",
    )

    assert [r.node for r in runs] == ["intake", "acknowledge", "diagnose", "report"]
    assert isinstance(final["report"], Reply)
    assert "ERR19 ở orders.init" in final["report"].text
    (written,) = list(reports_dir.glob("*.md"))
    assert "ERR19 ở orders.init" in written.read_text()


# --- replay_case.py, the tool that answers questions 3 and 4 -----------------


def test_a_captured_case_runs_through_the_real_source():
    """The point of capturing an answer rather than a list of lines: the
    parsing, the stamps, the stream merge and the `truncated` flag are the
    real ones, and only the socket is missing. A capture that stored lines
    would test the fake's own parsing and pass while `_streams` was broken.
    """
    import asyncio

    from plugins.backend.placement import Placement
    from plugins.backend.sources.logs import LokiSource
    from replay_case import CannedReads

    answer = {
        "streams": [{"labels": {}, "lines": ["2026-09-21T10:35:01Z ERROR boom"]}],
        "truncated": True,
    }
    source = LokiSource(server=CannedReads({"window": answer, "narrowed": answer}))

    read = asyncio.run(
        source.lines(
            Placement(env="production", service="s", cluster="c",
                      namespace="n", app="a"),
            since=REPORTED_AT, until=REPORTED_AT, limit=400,
        )
    )

    assert read.lines == ("ERROR boom",)
    assert read.truncated and read.oldest is not None


def test_a_case_key_nobody_reads_is_an_error_rather_than_a_silence():
    """**A typo here is invisible and undoes the run.** Renaming
    `correlation_id` to `correlationId` in the file made the dossier 18
    lines instead of 8 — the request matched by its endpoint path instead,
    pulling in every other caller — and the tool reported a clean run. A
    hand-written file is the only input this has."""
    from replay_case import case_params

    with pytest.raises(ValueError, match="correlationId"):
        case_params({
            "id": "x", "channel_id": "c", "reported_at": "2026-09-21T00:00:00",
            "reads": {}, "correlationId": "abc-123",
        })


def test_a_case_missing_what_it_needs_says_which():
    from replay_case import case_params

    with pytest.raises(ValueError, match="reads"):
        case_params({
            "id": "x", "channel_id": "c", "reported_at": "2026-09-21T00:00:00",
        })


def test_the_parameters_are_read_off_the_dataclass_not_the_class_body():
    """`__annotations__` is only what the class itself declares, so it would
    start dropping inherited parameters silently the day one of these grows
    a base class."""
    import dataclasses

    from plugins.backend.params import ApiIssueParams
    from replay_case import case_params

    kept = case_params({
        "id": "x", "channel_id": "c", "reported_at": "2026-09-21T00:00:00",
        "reads": {}, "summary": "s", "curl": "c", "correlation_id": "i",
        "environment": "dev", "response": "r", "endpoint": "/v1/x",
        "identifier": "device-1",
    })

    assert set(kept) == {f.name for f in dataclasses.fields(ApiIssueParams)}


def test_a_dev_case_is_captured_from_kubectl_and_replays_through_it():
    """Dev retention is the *reason* captured cases exist — a pod keeps only
    what it logged since its last restart. A capture mechanism that served
    production alone would cover the one environment that did not need it.
    """
    import asyncio

    from plugins.backend.placement import Placement
    from replay_case import canned_source

    name, source = canned_source({
        "source": "kubectl",
        "reads": {
            "window": "2026-09-21T10:35:01Z INFO quiet\n",
            "narrowed": "2026-09-21T10:35:01Z ERROR boom\n",
        },
    })

    read = asyncio.run(
        source.lines(
            Placement(env="dev", service="s", namespace="n",
                      pod_pattern="backend"),
            since=datetime(2026, 9, 21, 10, tzinfo=timezone.utc),
            until=datetime(2026, 9, 21, 11, tzinfo=timezone.utc),
            limit=400, needle="abc",
        )
    )

    assert name == "kubectl"
    assert read.lines == ("ERROR boom",)


def test_a_case_captured_from_somewhere_else_is_refused():
    from replay_case import canned_source

    with pytest.raises(ValueError, match="loki or kubectl"):
        canned_source({"source": "splunk", "reads": {}})


def test_a_captured_case_answers_the_two_reads_separately():
    """A capture holds what the back end returned for *each* read. Serving
    the window's answer to the narrowed read would hide exactly the fault
    this whole change was made for."""
    import asyncio

    from replay_case import CannedReads

    canned = CannedReads({"window": {"streams": []}, "narrowed": {"truncated": True}})

    window = asyncio.run(canned.call("loki_query_range", {"query": '{app="a"}'}))
    ours = asyncio.run(
        canned.call("loki_query_range", {"query": '{app="a"} |= "abc"'})
    )

    assert "streams" in window and "truncated" in ours


def test_a_superset_capture_serves_any_needle_the_loop_chooses():
    """A fixed pipeline captured one needle's answer; an agentic loop asks its
    own. The superset holds the whole window, so the canned source narrows it
    the way Loki does — any needle in the window is served, not just the one it
    was captured for, and a line outside the window is not."""
    import asyncio

    from plugins.backend.placement import Placement
    from plugins.backend.sources.logs import LokiSource
    from replay_case import CannedReads

    superset = [
        "2026-09-21T09:00:00Z ERROR boom user=old",   # before the window
        "2026-09-21T10:35:01Z ERROR boom user=abc",
        "2026-09-21T10:35:02Z INFO ok user=xyz",
        "2026-09-21T10:35:03Z ERROR boom user=def",
    ]
    src = LokiSource(server=CannedReads({"superset": superset}))
    place = Placement(env="production", service="s", cluster="c",
                      namespace="n", app="a")
    since = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
    until = datetime(2026, 9, 21, 11, tzinfo=timezone.utc)

    boom = asyncio.run(src.lines(place, since=since, until=until, limit=400,
                                 needle="boom"))
    xyz = asyncio.run(src.lines(place, since=since, until=until, limit=400,
                                needle="xyz"))
    gone = asyncio.run(src.lines(place, since=since, until=until, limit=400,
                                 needle="nope"))

    assert boom.lines == ("ERROR boom user=abc", "ERROR boom user=def")
    assert xyz.lines == ("INFO ok user=xyz",)
    assert gone.lines == ()


def test_a_superset_read_is_capped_at_the_limit_and_says_truncated():
    """The cap is the flag Loki sets and `kubectl` cannot — the canned source
    reports it when it drops a line, so a capped read does not read as a whole
    window."""
    import asyncio

    from plugins.backend.placement import Placement
    from plugins.backend.sources.logs import LokiSource
    from replay_case import CannedReads

    superset = [f"2026-09-21T10:35:0{i}Z ERROR boom {i}" for i in range(5)]
    src = LokiSource(server=CannedReads({"superset": superset}))
    place = Placement(env="production", service="s", cluster="c",
                      namespace="n", app="a")

    read = asyncio.run(src.lines(
        place,
        since=datetime(2026, 9, 21, 10, tzinfo=timezone.utc),
        until=datetime(2026, 9, 21, 11, tzinfo=timezone.utc),
        limit=2, needle="boom",
    ))

    assert len(read.lines) == 2 and read.truncated


def test_a_kubectl_superset_serves_the_needle_the_loop_chose():
    """The dev back end, served from a superset too — dev retention is the
    reason captured cases exist, so a superset that covered production alone
    would miss the one environment that needs it."""
    import asyncio

    from plugins.backend.placement import Placement
    from replay_case import canned_source

    name, source = canned_source({
        "source": "kubectl",
        "reads": {"superset": [
            "2026-09-21T10:35:01Z ERROR boom",
            "2026-09-21T10:35:02Z INFO quiet",
        ]},
    })

    read = asyncio.run(source.lines(
        Placement(env="dev", service="s", namespace="n", pod_pattern="backend"),
        since=datetime(2026, 9, 21, 10, tzinfo=timezone.utc),
        until=datetime(2026, 9, 21, 11, tzinfo=timezone.utc),
        limit=400, needle="boom",
    ))

    assert name == "kubectl" and read.lines == ("ERROR boom",)


def test_a_kubectl_superset_serves_a_needle_with_spaces_and_a_quote():
    """The `kubectl` remote is shell-quoted twice, so a needle carrying a space
    or an apostrophe must still be extracted whole — not read still-quoted (an
    empty result) nor split mid-token (a crash). Multi-word substrings are
    exactly what a loop searching log text picks, so this is the superset path's
    main case, not an edge."""
    import asyncio

    from plugins.backend.placement import Placement
    from replay_case import canned_source

    name, source = canned_source({
        "source": "kubectl",
        "reads": {"superset": [
            "2026-09-21T10:35:01Z ERROR connection refused to db",
            "2026-09-21T10:35:02Z WARN can't reach cache",
            "2026-09-21T10:35:03Z INFO quiet",
        ]},
    })
    place = Placement(env="dev", service="s", namespace="n",
                      pod_pattern="backend")
    since = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
    until = datetime(2026, 9, 21, 11, tzinfo=timezone.utc)

    spaced = asyncio.run(source.lines(place, since=since, until=until,
                                      limit=400, needle="connection refused"))
    quoted = asyncio.run(source.lines(place, since=since, until=until,
                                      limit=400, needle="can't"))

    assert spaced.lines == ("ERROR connection refused to db",)
    assert quoted.lines == ("WARN can't reach cache",)


def test_a_node_that_ended_the_run_does_not_read_as_one_that_passed_it_on():
    """`node_runs` records any `Action` as `ok`, because an `Action` carries
    no envelope. So a `resolve` that handed over — ending the whole run —
    printed exactly like a `resolve` that succeeded, and the first real use
    of this tool was ten minutes of reading the wrong thing."""
    from friday.sdk.workflow import NodeRun
    from replay_case import answers

    run = NodeRun(
        dag_name="backend.trace_problem", node="resolve", attempt=1,
        status="ok", reason="", duration_ms=6,
    )
    final = DAGState.empty().with_result("resolve", HandOver("no service row"))

    found = answers([run], final, wall_s=0.1)

    assert found["nodes"][0]["status"] == "handed over"
    assert found["nodes"][0]["reason"] == "no service row"


def test_a_node_that_failed_is_named_as_a_seam_that_broke():
    """Question 4. `empty` and `skipped` are nodes doing their job with
    nothing to work on; `error` and `timed_out` are the seams."""
    from friday.sdk.workflow import NodeRun
    from replay_case import answers

    def run(node, status, reason=""):
        return NodeRun(
            dag_name="backend.trace_problem", node=node, attempt=1,
            status=status, reason=reason, duration_ms=1,
        )

    found = answers(
        [run("find_request_log", "error", "ssh: no route to host"),
         run("read_failing_code", "empty", "no frame")],
        DAGState.empty(), wall_s=0.1,
    )

    assert found["broke"] == ["find_request_log: ssh: no route to host"]


# --- Loki, against what the real server actually answers ---------------------

#: Captured from `devops-generic` on 2026-09-21, trimmed to two streams and
#: three lines. A shape this code parses has to be checked against the thing
#: that produces it, not against its documentation — three of the guesses in
#: the first version were wrong and this is what found them.
LOKI_ANSWER = """{"result_type":"streams","returned":3,"limit":3,
"truncated":true,"streams":[
 {"labels":{"apero_cluster":"oregon-llm","namespace":"vsl",
            "app":"backend-reelme-v2","pod":"backend-reelme-v2-56d5df5c57-jc5fr"},
  "lines":["2026-09-21T08:27:49.455Z {\\"level\\":\\"INFO\\",\\"path\\":\\"/v2/daily-shot\\"}"]},
 {"labels":{"apero_cluster":"oregon-llm","namespace":"vsl",
            "app":"backend-reelme-v2","pod":"backend-reelme-v2-56d5df5c57-dtv9q"},
  "lines":["2026-09-21T08:27:49.188Z {\\"level\\":\\"INFO\\",\\"path\\":\\"/v1/library\\"}",
           "2026-09-21T08:27:49.163Z {\\"level\\":\\"INFO\\",\\"path\\":\\"/v1/workflow\\"}"]}]}"""


def test_lokis_streams_are_merged_into_one_line_of_time():
    """One stream per replica. A dossier that interleaves replicas in
    whatever order they arrived is one whose surrounding lines belong to a
    different process than the line they surround — and `distil` keeps ±2
    lines around what it finds."""
    from plugins.backend.sources.logs import _streams

    found = _streams(LOKI_ANSWER)

    assert found.lines[0].endswith('/v1/workflow"}')
    assert found.lines[-1].endswith('/v2/daily-shot"}')
    assert found.oldest.isoformat().startswith("2026-09-21T08:27:49.163")


def test_the_timestamp_comes_off_and_the_cap_is_reported():
    from plugins.backend.sources.logs import _streams

    found = _streams(LOKI_ANSWER)

    assert not any(line.startswith("2026-") for line in found.lines)
    assert found.truncated is True


def test_an_answer_loki_never_gave_is_empty_rather_than_a_crash():
    """A changed shape should read as "Loki said nothing I understood",
    which the node reports, not as a graph that died."""
    from plugins.backend.sources.logs import _streams

    assert _streams("not json").lines == ()
    assert _streams('{"streams": null}').lines == ()


def _kubectl_returning(count: int):
    """A `SshKubectlSource` whose back end hands back `count` stamped lines.

    Through `lines()` rather than through `_within` directly: the thing
    under test is the *inference* that a read which came back exactly as
    long as its bound was cut short, and a test that hands `_within` the
    answer has already made that inference itself.
    """
    import asyncio

    from plugins.backend.placement import Placement
    from plugins.backend.sources.logs import SshKubectlSource

    class Source(SshKubectlSource):
        async def _run(self, remote):
            if "get pods" in remote:
                return "pod/backend-1\n"
            return "".join(
                f"2026-09-20T04:40:{i:02d}Z line {i}\n" for i in range(count)
            )

    return asyncio.run(
        Source().lines(
            Placement(env="dev", service="s", namespace="n",
                      pod_pattern="backend"),
            since=REPORTED_AT - timedelta(hours=1), until=REPORTED_AT,
            limit=5,
        )
    )


def test_kubectl_says_it_capped_because_kubectl_will_not():
    """Loki says `truncated: true`; `kubectl` says nothing at all. Exactly
    `limit` lines back from a bound of `limit` is the only signal there is,
    and without inferring it the honest sentence is dead on dev — which is
    where every case ticket 00 has is logged."""
    assert _kubectl_returning(5).truncated


def test_a_read_that_came_back_short_of_its_bound_is_not_called_capped():
    """The other half: reporting every read as a sample would make the
    sentence noise, and noise is what stops being read."""
    assert not _kubectl_returning(2).truncated


def test_both_parsers_say_which_span_they_handed_over():
    """`newest` is what lets the capped sentence name a span. A parser that
    populated only `oldest` would leave it saying "an unknown part", which
    is the sentence it replaced."""
    from plugins.backend.sources.logs import _streams, _within

    parsed = _streams(
        '{"streams":[{"labels":{},"lines":['
        '"2026-09-21T10:38:20Z first","2026-09-21T10:39:44Z last"]}],'
        '"truncated":true}'
    )
    clipped = _within(
        ["2026-09-21T10:38:20Z first", "2026-09-21T10:39:44Z last"],
        since=datetime(2026, 9, 21, 10, tzinfo=timezone.utc),
        until=datetime(2026, 9, 21, 11, tzinfo=timezone.utc),
    )

    for read in (parsed, clipped):
        assert read.oldest.minute == 38 and read.newest.minute == 39


def test_loki_asks_its_back_end_for_the_lines_that_carry_the_needle():
    """LogQL's line filter, which runs where the log is. The difference
    measured against production: 400 lines covering three per cent of the
    window and not containing the request, against two lines containing all
    of it with `truncated: false`."""
    import asyncio

    from friday.sdk.sources import Reads
    from plugins.backend.placement import Placement
    from plugins.backend.sources.logs import LokiSource

    class Server:
        asked: dict = {}

        async def call_tool(self, tool, arguments):
            Server.asked = arguments
            return '{"streams":[],"truncated":false}'

    source = LokiSource(server=Reads(Server(), LokiSource.TOOLS))
    asyncio.run(
        source.lines(
            Placement(env="production", service="s", cluster="c",
                      namespace="n", app="a"),
            since=REPORTED_AT, until=REPORTED_AT, limit=400,
            # A needle carrying a quote, so the escaping is guarded **where
            # it is used** and not only where it is defined: a call site
            # that dropped `_logql` passed a test that only ever asked it
            # about a uuid.
            needle='abc "x" 123',
        )
    )

    assert Server.asked["query"].endswith(r'|= "abc \"x\" 123"')


def test_a_needle_carrying_a_quote_is_escaped_rather_than_ending_the_filter():
    """LogQL's filter is a Go quoted string. A needle with a `"` in it would
    otherwise close the literal and change the query instead of being
    searched for."""
    from plugins.backend.sources.logs import _logql

    assert _logql('a"b') == 'a\\"b'
    assert _logql("a\\b") == "a\\\\b"


def test_kubectl_searches_the_whole_window_rather_than_its_tail():
    """`--tail` is applied by the API server *before* anything downstream
    sees a line, so `--tail=400 | grep` searches the newest 400 lines and
    not the window. The whole window, narrowed on the far side by `grep`,
    searches all of it — and `head` keeps the answer bounded."""
    import asyncio

    from plugins.backend.placement import Placement
    from plugins.backend.sources.logs import SshKubectlSource

    ran: list[str] = []

    class Source(SshKubectlSource):
        async def _run(self, remote):
            ran.append(remote)
            return "pod/backend-1\n" if "get pods" in remote else ""

    asyncio.run(
        Source().lines(
            Placement(env="dev", service="s", namespace="n",
                      pod_pattern="backend"),
            since=REPORTED_AT, until=REPORTED_AT, limit=400, needle="abc-123",
        )
    )

    assert "--tail=-1" in ran[-1] and "grep -F -- abc-123" in ran[-1]
    assert "head -n 400" in ran[-1]


def test_a_needle_reaches_the_shell_quoted():
    """It comes off a curl the reporter pasted. `shlex.quote` is what stands
    between that and a command line."""
    import asyncio

    from plugins.backend.placement import Placement
    from plugins.backend.sources.logs import SshKubectlSource

    ran: list[str] = []

    class Source(SshKubectlSource):
        async def _run(self, remote):
            ran.append(remote)
            return "pod/backend-1\n" if "get pods" in remote else ""

    asyncio.run(
        Source().lines(
            Placement(env="dev", service="s", namespace="n",
                      pod_pattern="backend"),
            since=REPORTED_AT, until=REPORTED_AT, limit=10,
            needle="x; rm -rf /",
        )
    )

    # Parsed the way a shell parses it, rather than pattern-matched: the
    # property is that the needle stays one word, and an assertion about
    # quote characters can hold while the injection still splits.
    import shlex

    outer = shlex.split(ran[-1])
    assert outer[:4] == ["bash", "-o", "pipefail", "-c"]
    assert "x; rm -rf /" in shlex.split(outer[4])
    assert "rm" not in shlex.split(outer[4])


def test_a_failing_kubectl_is_not_hidden_by_the_pipe_that_narrows_it():
    """A pipeline exits with its *last* command's status, so `head`
    returning 0 would hide a pod that has gone, an RBAC denial or the wrong
    container — each arriving as empty output and reported as "the window
    holds nothing about this request". That is the confusion between *not
    found* and *not searched* this node already exists to prevent.

    `grep` matching nothing is the one non-zero that is not a failure, so it
    is allowed explicitly rather than by leaving the whole pipe unchecked.
    """
    import asyncio

    from plugins.backend.placement import Placement
    from plugins.backend.sources.logs import SshKubectlSource

    ran: list[str] = []

    class Source(SshKubectlSource):
        async def _run(self, remote):
            ran.append(remote)
            return "pod/backend-1\n" if "get pods" in remote else ""

    asyncio.run(
        Source().lines(
            Placement(env="dev", service="s", namespace="n",
                      pod_pattern="backend"),
            since=REPORTED_AT, until=REPORTED_AT, limit=10, needle="abc-123",
        )
    )

    assert "-o pipefail" in ran[-1]
    assert "|| true" in ran[-1], "an empty match is not a failed read"


async def test_a_reader_may_not_call_a_tool_it_did_not_declare():
    """The operator's call, 2026-09-21: which tools may be called is code,
    not configuration. The server this narrows also offers `release_apply`,
    `release_rollback` and `godaddy_dns_edit_record` — and a guard a file can
    widen is one the file's next editor widens by accident."""
    from friday.sdk.sources import Reads
    from plugins.backend.sources.logs import LokiSource

    class Server:
        async def call_tool(self, tool, arguments):
            return tool

    narrowed = Reads(Server(), LokiSource.TOOLS)

    assert await narrowed.call("loki_query_range", {}) == "loki_query_range"
    with pytest.raises(PermissionError, match="release_rollback"):
        await narrowed.call("release_rollback", {})


def test_a_raw_server_cannot_be_handed_to_a_reader_by_mistake():
    """`Reads` is deliberately not a drop-in for a server: it answers `call`
    where a server answers `call_tool`, so an unnarrowed server fails at the
    first read instead of reaching the whole catalogue."""
    from friday.sdk.sources import Reads

    assert hasattr(Reads, "call") and not hasattr(Reads, "call_tool")


def test_what_a_server_is_filtered_to_is_read_off_the_readers():
    """A list beside the classes is a list that disagrees with them — so the
    set grows when a reader is added and by no other means."""
    from plugins.backend.sources import declared
    from plugins.backend.sources.db import DbSource
    from plugins.backend.sources.logs import LokiSource
    from plugins.backend.sources.release import ReleaseSource

    assert declared() == LokiSource.TOOLS | DbSource.TOOLS | ReleaseSource.TOOLS
    assert "execute_mongo_query" not in declared(), "no caller yet"


# --- a compiled frame is not the code anybody wrote (ticket 04) --------------


def _built(root, *, ts_lines=20, js_line=3, ts_line=11):
    """A tiny `dist/x.js` with a real source map back to `src/x.ts`."""
    import json

    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "dist").mkdir(parents=True, exist_ok=True)
    (root / "src" / "x.ts").write_text(
        "\n".join(f"ts line {i}" for i in range(1, ts_lines + 1))
    )
    (root / "dist" / "x.js").write_text(
        "\n".join(f"js line {i}" for i in range(1, 6))
    )
    # One segment on the `js_line`th generated line, pointing at `ts_line`.
    # Every field is a delta and `A` is zero, so the third field carries the
    # original line: `ts_line - 1` encoded, and this builds it by hand.
    def vlq(n: int) -> str:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        value, out = (abs(n) << 1) | (1 if n < 0 else 0), ""
        while True:
            digit, value = value & 31, value >> 5
            out += alphabet[digit | (32 if value else 0)]
            if not value:
                return out

    segment = "A" + "A" + vlq(ts_line - 1) + "A"
    (root / "dist" / "x.js.map").write_text(json.dumps({
        "version": 3, "file": "x.js", "sourceRoot": "",
        "sources": ["../src/x.ts"], "names": [],
        "mappings": ";" * (js_line - 1) + segment,
    }))


def test_a_compiled_frame_is_translated_back_to_the_source(tmp_path):
    """Measured on the operator's own clone: `workflow-credit.service.js:60`
    is `workflow-credit.service.ts:109`, forty-nine lines away. Mapping the
    file and keeping the line would hand `Diagnose` the wrong place and call
    it the throw site."""
    from plugins.backend.sources.code import original

    _built(tmp_path, js_line=3, ts_line=11)

    found = original(tmp_path / "dist" / "x.js", 3, tmp_path)

    assert found == ((tmp_path / "src" / "x.ts").resolve(), 11)


def test_a_compiled_file_with_no_map_beside_it_is_not_translated(tmp_path):
    """`None` means "read the built file and say so", never "guess"."""
    from plugins.backend.sources.code import original

    _built(tmp_path)
    (tmp_path / "dist" / "x.js.map").unlink()

    assert original(tmp_path / "dist" / "x.js", 3, tmp_path) is None


def test_a_map_that_does_not_parse_is_not_translated(tmp_path):
    from plugins.backend.sources.code import original

    _built(tmp_path)
    (tmp_path / "dist" / "x.js.map").write_text("{ not json")

    assert original(tmp_path / "dist" / "x.js", 3, tmp_path) is None


def test_a_map_naming_a_file_outside_the_clone_is_refused(tmp_path):
    """`repo_file` refuses a stack frame that climbs out of the clone, and a
    `.map` naming `../../../../etc/hosts` is the same climb by a quieter
    route. The rule was written at the top of that module and then not
    applied to the function added under it — found by review."""
    import json

    from plugins.backend.sources.code import original

    _built(tmp_path, js_line=3, ts_line=11)
    map_file = tmp_path / "dist" / "x.js.map"
    loaded = json.loads(map_file.read_text())
    loaded["sources"] = ["../" * 12 + "etc/hosts"]
    map_file.write_text(json.dumps(loaded))

    assert original(tmp_path / "dist" / "x.js", 3, tmp_path) is None


def test_a_map_whose_sources_hold_null_is_not_followed(tmp_path):
    """Legal in v3 beside `sourcesContent`, and it used to raise a
    `TypeError` out of the graph node rather than answering `None`."""
    import json

    from plugins.backend.sources.code import original

    _built(tmp_path)
    map_file = tmp_path / "dist" / "x.js.map"
    loaded = json.loads(map_file.read_text())
    loaded["sources"] = [None]
    map_file.write_text(json.dumps(loaded))

    assert original(tmp_path / "dist" / "x.js", 3, tmp_path) is None


def test_a_map_with_a_character_that_is_not_vlq_is_rejected_whole(tmp_path):
    """Not "decoded as far as it got". Every field is a delta on the last, so
    a half-read segment is dropped **along with the deltas it carried**, and
    every later segment is then computed from the wrong base — the map
    answers with a confident wrong line instead of nothing.

    So the corrupt segment here is on an *earlier* line than the one asked
    for, carrying a jump the answer would otherwise include. Decoding as far
    as it got returns line 2; rejecting the map returns nothing.
    """
    import json

    from plugins.backend.sources.code import original

    _built(tmp_path, js_line=3, ts_line=11)
    map_file = tmp_path / "dist" / "x.js.map"
    loaded = json.loads(map_file.read_text())
    # line 1: a whole segment jumping +40. line 2: the same, corrupted.
    # line 3: +1 from wherever the carry left off.
    loaded["mappings"] = "AACA;AA!QA;AACA".replace("!", "!")
    map_file.write_text(json.dumps(loaded))

    assert original(tmp_path / "dist" / "x.js", 3, tmp_path) is None


# --- what a code means (ticket 04, the repo's own docs) ---------------------

CODES_DOC = """# Error codes

Branch on `errorCode`, never on `message`.

## General

| Code     | Name                | Meaning                        |
| -------- | ------------------- | ------------------------------ |
| `ERR16`  | `INVALID_INPUT`     | Invalid input                  |
| `ERR19`  | `INTERNAL_ERROR`    | Something failed server-side   |

## Midas

| Code     | Name                | Meaning                        |
| `ERR306` | `BILLING_ERROR`     | Midas refused the charge       |
"""


def test_only_the_codes_that_turned_up_are_read_out_of_the_doc(tmp_path):
    """Ticket 16 measured 2,104 of 2,104 HTTP 500s carrying `ERR19` — the
    generic code — so the doc is what turns a code into an answer. The whole
    doc is 271 lines; a run needs the three lines it saw."""
    from plugins.backend.sources.code import meanings

    doc = tmp_path / "docs" / "error-codes.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(CODES_DOC)

    found = meanings(doc, ("ERR19", "ERR306"), tmp_path)

    assert found == {
        "ERR19": "INTERNAL_ERROR — Something failed server-side",
        "ERR306": "BILLING_ERROR — Midas refused the charge",
    }


def test_a_code_the_doc_does_not_list_is_absent_rather_than_invented(tmp_path):
    from plugins.backend.sources.code import meanings

    doc = tmp_path / "error-codes.md"
    doc.write_text(CODES_DOC)

    assert meanings(doc, ("ERR999",), tmp_path) == {}


def test_a_doc_outside_the_clone_is_not_read(tmp_path):
    """`error_codes_doc` is a path from a row somebody typed, and this module
    checks a path before it opens it — the same rule as a stack frame."""
    from plugins.backend.sources.code import meanings

    outside = tmp_path / "outside" / "error-codes.md"
    outside.parent.mkdir(parents=True)
    outside.write_text(CODES_DOC)
    (tmp_path / "clone").mkdir()

    assert meanings(outside, ("ERR19",), tmp_path / "clone") == {}


def test_a_two_column_table_is_read_as_well_as_a_three(tmp_path):
    """The real document has both — 130 rows of `code | name | meaning` and
    69 of `code | meaning`. Wanting three silently dropped every Midas code,
    `ERR306` among them, which ticket 16 counted 3,455 times in 30 days."""
    from plugins.backend.sources.code import meanings

    doc = tmp_path / "error-codes.md"
    doc.write_text(
        "| Code | Meaning |\n"
        "| ---- | ------- |\n"
        "| `ERR306` | Content pack required |\n"
    )

    assert meanings(doc, ("ERR306",), tmp_path) == {
        "ERR306": "Content pack required"
    }


# --- reading the code that is actually running (ticket 04) -------------------


def test_the_running_tag_is_read_out_of_the_release_answer():
    """Measured 2026-09-21: `release_status` answers 104,761 characters, of
    which the tag is one field. It is read out at the source so nothing above
    ever holds a rendered Helm chart — in memory or in a prompt."""
    import asyncio
    import json

    from plugins.backend.sources.release import ReleaseSource

    class Server:
        async def call(self, tool, arguments):
            assert tool == "release_status"
            return json.dumps({
                "status": {"config": {"image": {
                    "repository": "…/backend-reelme-v2", "tag": "0.4.4",
                }}},
                "manifest": "x" * 50_000,
            })

    got = asyncio.run(ReleaseSource(server=Server()).running_tag("p", "prod"))

    assert got == "0.4.4"


def test_not_knowing_which_version_runs_is_an_answer_not_a_failure():
    """A node that raised here would turn "I could not check which version
    runs" into a failed investigation."""
    import asyncio

    from plugins.backend.sources.release import ReleaseSource

    class Broken:
        async def call(self, tool, arguments):
            raise RuntimeError("no route to host")

    class Odd:
        async def call(self, tool, arguments):
            return '{"status": {"config": {}}}'

    assert asyncio.run(ReleaseSource(server=Broken()).running_tag("p", "prod")) == ""
    assert asyncio.run(ReleaseSource(server=Odd()).running_tag("p", "prod")) == ""


def test_reading_at_a_ref_never_moves_the_operators_clone(tmp_path):
    """The clone is open in their editor. Moving its HEAD to answer a
    question is the one thing this must not do, and a detached worktree is
    disk, cleanup and a failure mode for a read that needs none of it."""
    import subprocess

    from plugins.backend.sources.code import at_ref

    root = tmp_path / "clone"
    root.mkdir()
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(root), *a], capture_output=True, text=True, check=True
    )
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (root / "a.ts").write_text("released\n")
    run("add", "a.ts")
    run("commit", "-qm", "one")
    run("tag", "1.0.0")
    (root / "a.ts").write_text("edited since\n")

    before = run("rev-parse", "HEAD").stdout

    assert at_ref(str(root), root / "a.ts", "1.0.0") == "released\n"
    assert (root / "a.ts").read_text() == "edited since\n", "the tree is untouched"
    assert run("rev-parse", "HEAD").stdout == before, "HEAD did not move"


def test_a_ref_that_could_be_read_as_an_option_never_reaches_git(tmp_path, monkeypatch):
    """The ref comes out of an MCP answer and goes onto a command line.
    `git show` takes no `--` before its `rev:path`, so a leading dash would
    be read as an option.

    Asserts git is never *invoked*, not that the call returned `None` — a
    bad ref makes `git show` fail and return `None` too, so the weaker
    assertion passed with the guard deleted."""
    from plugins.backend.sources import code as code_source

    ran = []
    monkeypatch.setattr(
        code_source.subprocess, "run", lambda *a, **k: ran.append(a) or (_ for _ in ()).throw(AssertionError("git was run"))
    )

    assert code_source.at_ref(str(tmp_path), tmp_path / "a.ts", "--upload-pack=x") is None
    assert code_source.at_ref(str(tmp_path), tmp_path / "a.ts", "") is None
    assert ran == []


# --- the shape forces the question (ticket 05) ------------------------------


def _intake_state(
    *, env="dev", service="s", cluster="", namespace="", app="",
    pod_pattern="p", repo_path="", error_code_doc="",
    candidates=(), request_text="500 khi init đơn",
) -> DAGState:
    """An `intake` envelope reads-mode (and `acknowledge`/`report`) needs —
    the JSON-round-tripped shape `intake_of` reads back."""
    return DAGState.empty().with_result("intake", {
        "status": "ok", "reason": "",
        "intake": {
            "request_text": request_text,
            "reported_at": REPORTED_AT.isoformat(),
            "hints": {"uuids": [], "artifacts": []},
            "domain": {
                "env": env, "service": service, "cluster": cluster,
                "namespace": namespace, "app": app, "pod_pattern": pod_pattern,
                "clone_path": repo_path, "repo_path": repo_path,
                "error_code_doc": error_code_doc,
                "container_roots": [], "dbs": [], "candidates": list(candidates),
                "correlation_id": None, "curl_artifact_id": None,
                "response_artifact_id": None,
            },
            "memory": [], "skills": [],
        },
    })

def _reads_state():
    """A placement reads-mode needs, off a real `intake` envelope."""
    return _intake_state(env="dev", service="s", pod_pattern="p")


def _reading_deps(db):
    """Deps whose `read_log` finds one real line, so a gate test's `refs`
    cite something `Evidence` actually holds rather than an invented id."""
    return deps_for(db, extra={
        "log_sources": {"kubectl": FakeSource(answers=[["ERROR boom"]])},
    })


def _answered_after_reading(**kw):
    """A reads-mode harness that reads one line — so `L1` is a real
    citation — then answers with a fixed `Diagnosis`. The reads-mode
    equivalent of the old `_answering` dossier stub."""

    def make(*, tools):
        class Said:
            last_error = None

            async def run_structured(self, prompt, **_):
                await tools[0].fn(needle="boom")
                return Diagnosis(**kw)

        return Said()

    return make


async def test_conclusive_without_a_rejected_alternative_is_refused(db):
    """Spec, tier 1 of self-questioning: the answer shape forces it. A cause
    nothing was weighed against is the first thing the evidence suggested —
    which is exactly the answer a reader cannot tell from a considered one."""
    result = await diagnose_node(
        make_harness=_answered_after_reading(
            cause="x", confidence="certain", conclusive=True, refs=["L1"],
        ),
        agent="backend.diagnose",
    ).run(_reads_state(), _reading_deps(db))

    assert status_of(result) == "empty"
    assert "ruled out" in result["reason"]


async def test_an_alternative_with_no_reason_does_not_satisfy_the_gate(db):
    """An empty hypothesis rules nothing out and a reason with no substance
    is an assertion. Counting the field's existence would let the gate be
    satisfied by the shape rather than by the thinking."""
    result = await diagnose_node(
        make_harness=_answered_after_reading(
            cause="x", confidence="certain", conclusive=True, refs=["L1"],
            alternatives_rejected=[{"hypothesis": "   ", "why": ""}, "not a dict"],
        ),
        agent="backend.diagnose",
    ).run(_reads_state(), _reading_deps(db))

    assert status_of(result) == "empty"
    assert "ruled out" in result["reason"]


async def test_the_reading_loop_can_hand_over_instead_of_answering(db):
    """A reads-mode diagnosis can end by handing the case to the operator: the
    model calls `hand_over(reason)` and the node returns that `HandOver` Action
    rather than a diagnosis. It may hand over without reading anything (a case
    it cannot investigate at all), so the 'answered without reading' gate must
    not turn it into an empty envelope."""

    class HandsOver:
        last_error = None

        async def run_structured(self, prompt, **_):
            return HandOver("this needs a migration I may not run")

    node = diagnose_node(
        make_harness=lambda *, tools: HandsOver(), agent="backend.diagnose"
    )
    state = _intake_state(
        env="production", service="s", cluster="c", namespace="n", app="a",
    )

    result = await node.run(state, deps_for(db))

    assert isinstance(result, HandOver)
    assert "migration" in result.reason


def test_a_hand_over_from_diagnose_ends_the_walk_instead_of_reaching_report():
    """A `HandOver` the reads loop returns is the run's decision — the
    `diagnose -> report` edge must NOT fire on it. If it did, `report` sees a
    non-dict in the diagnose slot, produces its own generic hand-over, and the
    model's reason is lost. A normal envelope still advances to `report`."""
    dag = _dag()

    handed = DAGState.empty().with_result("diagnose", HandOver("needs a migration"))
    assert dag.next_after("diagnose", handed) is None

    diagnosed = DAGState.empty().with_result(
        "diagnose", {"status": "ok", "reason": "", "diagnosis": {}}
    )
    assert dag.next_after("diagnose", diagnosed) == "report"

    # An `empty`/`error` envelope must still reach `report` — that is what turns
    # "nothing was diagnosed" into the operator's brief.
    empty = DAGState.empty().with_result("diagnose", {"status": "empty", "reason": "x"})
    assert dag.next_after("diagnose", empty) == "report"


def test_ask_reporter_returns_an_ask_action():
    """The terminal tool the reads loop calls when a missing piece blocks it —
    maps to `Action=Ask`, whose text is sent to the reporter (ticket 04)."""
    from plugins.backend.graph.diagnose import ask_reporter

    # `@tool` wraps it in a ToolSpec; `.fn` is the underlying function.
    result = ask_reporter.fn("Ban gui giup correlationId duoc khong?")

    assert isinstance(result, Ask)
    assert result.text == "Ban gui giup correlationId duoc khong?"


async def test_the_reading_loop_can_ask_the_reporter_instead_of_answering(db):
    """A reads-mode diagnosis can end by asking the reporter for a missing
    piece: the model calls `ask_reporter(question)` and the node returns that
    `Ask` Action rather than a diagnosis. Like `hand_over`, it may ask without
    reading anything, so the 'answered without reading' gate must not turn it
    into an empty envelope."""

    class Asks:
        last_error = None

        async def run_structured(self, prompt, **_):
            return Ask("Ban dung moi truong nao, production hay dev?")

    node = diagnose_node(
        make_harness=lambda *, tools: Asks(), agent="backend.diagnose"
    )
    state = _intake_state(
        env="production", service="s", cluster="c", namespace="n", app="a",
    )

    result = await node.run(state, deps_for(db))

    assert isinstance(result, Ask)
    assert "moi truong" in result.text


def test_an_ask_from_diagnose_ends_the_walk_instead_of_reaching_report():
    """An `Ask` the reads loop returns is the run's decision — the
    `diagnose -> report` edge must NOT fire on it, exactly as for `HandOver`;
    otherwise `report` swallows the question and asks its own generic one."""
    dag = _dag()

    asked = DAGState.empty().with_result("diagnose", Ask("which env?"))
    assert dag.next_after("diagnose", asked) is None


async def test_a_weighed_conclusive_answer_is_reported(db):
    result = await diagnose_node(
        make_harness=_answered_after_reading(
            cause="x", confidence="certain", conclusive=True, refs=["L1"],
            alternatives_rejected=[
                {"hypothesis": "downstream timeout", "why": "no timeout line",
                 "ref": "L1"}
            ],
        ),
        agent="backend.diagnose",
    ).run(_reads_state(), _reading_deps(db))

    assert status_of(result) == "ok"
    assert result["diagnosis"]["alternatives_rejected"][0]["ref"] == "L1"
    assert result["quotes"] == ["ERROR boom"], "code puts the text back"


async def test_an_alternative_pointing_at_a_line_it_was_not_shown_voids_it(db):
    """It is shown to the operator as evidence something was ruled out, so an
    id naming no line is the same invention the main refs are checked for. A
    gate that checked half the answer is a gate a model learns the shape of."""
    result = await diagnose_node(
        make_harness=_answered_after_reading(
            cause="x", confidence="certain", conclusive=True, refs=["L1"],
            alternatives_rejected=[{"hypothesis": "y", "why": "z", "ref": "L99"}],
        ),
        agent="backend.diagnose",
    ).run(_reads_state(), _reading_deps(db))

    assert status_of(result) == "empty"
    assert "L99" in result["reason"]


async def test_a_tentative_answer_needs_no_alternative(db):
    """`conclusive: false` costs nothing, and the gate is the claim of
    certainty — not a tax on every answer."""
    result = await diagnose_node(
        make_harness=_answered_after_reading(
            cause="có thể do cache", confidence="likely", conclusive=False,
            refs=["L1"],
        ),
        agent="backend.diagnose",
    ).run(_reads_state(), _reading_deps(db))

    assert status_of(result) == "ok"


def test_the_model_is_told_both_to_fill_it_and_what_happens_if_it_does_not():
    """A rule enforced in code and absent from the prompt is a rule the model
    discovers by having its whole answer thrown away.

    Two separate things, asserted separately: the instruction to name an
    alternative, and the warning that claiming `conclusive` without one is
    refused. Asserting only the field name passed with the warning deleted,
    because the instruction mentions it too."""
    from plugins.backend.graph.prompt import build_instructions

    said = build_instructions()

    assert "alternatives_rejected" in said, "it is asked for"
    # The gate's own clause, not merely the words "conclusive" and "refused"
    # — both appear elsewhere in these instructions, so the looser assertion
    # stayed green with this warning deleted.
    assert "empty `alternatives_rejected` is refused" in said


def test_git_failing_is_no_ref_rather_than_an_exception(tmp_path, monkeypatch):
    """Its docstring promises `None` for "git not on PATH", and that was not
    true: `FileNotFoundError` and `TimeoutExpired` both came out of here, and
    a caller that read the promise and did not guard was a caller this
    function misled — `read_code` was exactly that caller."""
    import subprocess

    from plugins.backend.sources import code as code_source

    def explode(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(code_source.subprocess, "run", explode)
    assert code_source.at_ref(str(tmp_path), tmp_path / "a.ts", "1.0.0") is None

    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=20)

    monkeypatch.setattr(code_source.subprocess, "run", hang)
    assert code_source.at_ref(str(tmp_path), tmp_path / "a.ts", "1.0.0") is None


def test_check_deps_refuses_a_deps_factory_that_forgets_a_required_field():
    """The boot guard (ticket 13, Rule 13): a task type whose deps factory omits
    a required field — `sender`, whose absence would send a mid-run row as the
    wrong identity or none — fails at boot, where a config error belongs, not
    mid-investigation on a task nobody is watching. Delete `check_deps` and
    nothing catches it until the run."""
    from friday.kernel.config import ConfigError
    from friday.kernel.dag import registry
    from friday.kernel.dag.router import check_deps
    from friday.sdk.plugin import TaskTypeSpec
    from friday.sdk.workflow import DAG, Deps, Node

    def forgets_sender(base: Deps) -> Deps:
        return ApiIssueDeps(task=base.task, db=base.db, approver="x")  # no sender

    dag = DAG(name="bad", nodes=(Node(name="n", run=lambda s, d: None),))
    registry.clear()
    registry.register_task_type(
        TaskTypeSpec(name="bad", params=ApiIssueParams, deps=forgets_sender),
        dag=dag,
    )
    try:
        with pytest.raises(ConfigError, match="deps factory"):
            check_deps()
    finally:
        registry.clear()


# --- intake (ticket 03: built, NOT wired — ticket 6 puts it on the graph) ----


async def _task_with_text(
    db, text: str, *, channel_id: str = "watched", code: tuple[str, ...] = ()
) -> int:
    """A real task whose transcript `original_text_for` actually reads —
    Intake's `request_text` comes from there, not from `state["prepare"]`
    (this node runs with no extractor upstream of it). `code` mirrors
    `test_artifacts.py`'s `_curl_event`: a verbatim span becomes an artifact
    reference in what `original_text_for` hands back."""
    from conftest import make_event

    conversation = ConversationId("fake", channel_id)
    task = await db.create_task(
        conversation=conversation, type="backend.trace_problem", state="pending",
        confidence=0.9, params={},
    )
    event = replace(
        make_event(channel_id=channel_id, message_id=f"m{task.id}", text=text),
        code=code,
    )
    await db.record_message(event)
    await db.mark_triaged(event, task.id, decision={"type": "backend.trace_problem"})
    return task.id


async def test_intake_makes_no_model_call():
    """No `agent`, no harness parameter — a model call is not something this
    node's construction can even make."""
    from plugins.backend.graph.intake import intake_node

    import inspect

    node = intake_node(core_intake)

    assert node.agent is None
    assert list(inspect.signature(node.run).parameters) == ["state", "deps"]


async def test_intake_resolves_a_named_service_and_its_placement(db):
    from plugins.backend.graph.intake import intake_node

    await write_rows(db, env="dev", repo="/clone/reelme")
    task_id = await _task_with_text(
        db,
        "loi roi anh\n"
        f"```\n{CURL}\n```\n"
        "correlationId 8f14e45f-ceea-467a-9b3a-1e0e4a1b2c3d, "
        "service backend-reelme-v2 dang tra 500",
        code=(CURL,),
    )

    result = await intake_node(core_intake).run(
        DAGState.empty(), deps_for(db, task_id=task_id)
    )

    assert status_of(result) == "ok"
    placement = result["intake"]["domain"]
    assert placement["env"] == "dev"
    assert placement["service"] == "backend-reelme-v2"
    assert placement["namespace"] == "dev"
    assert placement["pod_pattern"] == "backend-reelme-v2"
    assert placement["clone_path"] == "/clone/reelme"
    assert placement["repo_path"] == "/clone/reelme"
    assert placement["candidates"] == []


async def test_intake_never_guesses_a_vague_service(db):
    """Zero matches — the room's candidate set, not a guess (the (c)->(a)
    hybrid fork, ticket 03)."""
    from plugins.backend.graph.intake import intake_node

    await write_rows(db, env="dev")
    task_id = await _task_with_text(db, "co loi 500 nhung khong biet service nao")

    result = await intake_node(core_intake).run(
        DAGState.empty(), deps_for(db, task_id=task_id)
    )

    placement = result["intake"]["domain"]
    assert placement["service"] == ""
    assert placement["candidates"] == ["backend-reelme-v2"]


async def _add_mirror_service(db):
    state = FridayState(channel_id="watched", agent="admin")
    await db.memory_add(
        state, "the ReelMe v2 backend, mirrored", kind="backend.service",
        origin=MemoryOrigin.ADMIN,
        data={
            "name": "backend-reelme-v2-mirror",
            "project": "reelme",
            "prod": {"cluster": "vultr-ailab", "namespace": "sw",
                     "app": "backend-reelme-v2-mirror"},
            "dev": {"kube_context": "dev", "namespace": "dev",
                    "pod_pattern": "backend-reelme-v2-mirror"},
        },
    )


async def test_intake_never_guesses_when_several_services_match(db):
    """Two services named as distinct tokens in the same text is as much a
    guess as none — the candidate set, not whichever sorted first."""
    from plugins.backend.graph.intake import intake_node

    await write_rows(db, env="dev")
    await _add_mirror_service(db)
    task_id = await _task_with_text(
        db, "backend-reelme-v2 va backend-reelme-v2-mirror deu dang tra 500"
    )

    result = await intake_node(core_intake).run(
        DAGState.empty(), deps_for(db, task_id=task_id)
    )

    placement = result["intake"]["domain"]
    assert placement["service"] == ""
    assert set(placement["candidates"]) == {
        "backend-reelme-v2", "backend-reelme-v2-mirror",
    }


async def test_intake_resolves_the_more_specific_name_over_its_prefix(db):
    """A name that is a prefix of another (`backend-reelme-v2` ⊂
    `…-mirror`) must not force the candidate set: naming the mirror as a whole
    token resolves the mirror, not both. Guards the token-boundary match — a
    substring match would resolve neither (both would match) and go red."""
    from plugins.backend.graph.intake import intake_node

    await write_rows(db, env="dev")
    await _add_mirror_service(db)
    task_id = await _task_with_text(db, "backend-reelme-v2-mirror dang tra 500")

    result = await intake_node(core_intake).run(
        DAGState.empty(), deps_for(db, task_id=task_id)
    )

    placement = result["intake"]["domain"]
    assert placement["service"] == "backend-reelme-v2-mirror"
    assert placement["candidates"] == []


async def test_intake_does_not_resolve_a_short_name_inside_a_host(db):
    """A service named `api` must NOT match inside the host
    `api-reelme-v2.dev…` in a pasted URL — a substring match would silently
    resolve the wrong service (M1); the whole-token match yields the candidate
    set instead. Guard goes red if the match is a substring again."""
    from plugins.backend.graph.intake import intake_node

    await write_rows(db, env="dev")
    state = FridayState(channel_id="watched", agent="admin")
    await db.memory_add(
        state, "the gateway", kind="backend.service", origin=MemoryOrigin.ADMIN,
        data={
            "name": "api",
            "project": "reelme",
            "prod": {"cluster": "vultr-ailab", "namespace": "sw", "app": "api"},
            "dev": {"kube_context": "dev", "namespace": "dev", "pod_pattern": "api"},
        },
    )
    # URL inline (not a code artifact) so the host stays in request_text.
    task_id = await _task_with_text(
        db, "loi 500 tren https://api-reelme-v2.dev.aperogroup.ai/v1/pod/orders/init"
    )

    result = await intake_node(core_intake).run(
        DAGState.empty(), deps_for(db, task_id=task_id)
    )

    placement = result["intake"]["domain"]
    assert placement["service"] == ""            # not "api"
    assert "api" in placement["candidates"]


async def test_intake_context_carries_reported_at_and_container_roots_and_hints(db):
    from plugins.backend.graph.intake import intake_node

    task_id = await _task_with_text(
        db,
        "loi roi\n"
        f"```\n{CURL}\n```\n"
        "correlationId 8f14e45f-ceea-467a-9b3a-1e0e4a1b2c3d",
        code=(CURL,),
    )

    result = await intake_node(core_intake).run(
        DAGState.empty(), deps_for(db, task_id=task_id)
    )

    intake = result["intake"]
    assert intake["reported_at"]  # non-empty ISO timestamp
    assert intake["domain"]["container_roots"] == list(DEFAULT_CONTAINER_ROOTS)
    assert intake["hints"]["uuids"] == ["8f14e45f-ceea-467a-9b3a-1e0e4a1b2c3d"]
    assert intake["domain"]["correlation_id"] == "8f14e45f-ceea-467a-9b3a-1e0e4a1b2c3d"
    assert intake["domain"]["curl_artifact_id"]  # the curl became an artifact


def test_placement_identity_is_stable_when_memory_or_skills_change():
    """Only env/service/clone/repo invalidate a running investigation
    (ticket 01's checkpoint discard key) — memory, skills and hints changing
    must not."""
    from friday.sdk.intake import Hints, IntakeContext
    from plugins.backend.placement import Placement

    placement = Placement(env="dev", service="backend-reelme-v2", repo_path="/r")
    a = IntakeContext(request_text="x", reported_at="t", hints=Hints(), domain=placement)
    b = IntakeContext(
        request_text="x", reported_at="t", hints=Hints(uuids=("u",)),
        domain=replace(placement, correlation_id="u"),
        memory=("a fact",), skills=("a skill",),
    )

    assert a.identity == b.identity == ("dev", "backend-reelme-v2", "", "/r")


async def test_intake_retrieval_lands_matched_skills_and_facts(db):
    from plugins.backend.graph.intake import intake_node

    await write_rows(db, env="dev")
    state = FridayState(channel_id="watched", agent="admin")
    await db.memory_add(
        state, "backend-reelme-v2 has flaky retries", kind="fact",
        origin=MemoryOrigin.ADMIN,
    )
    await db.memory_add(
        state, "read backend-reelme-v2's queue depth first", kind="skill",
        origin=MemoryOrigin.ADMIN, key="reelme-queue",
        data={"when": {"service": ["backend-reelme-v2"], "error_codes": [],
                        "path_patterns": [], "keywords": []}},
    )
    task_id = await _task_with_text(db, "backend-reelme-v2 dang tra 500")

    result = await intake_node(core_intake).run(
        DAGState.empty(), deps_for(db, task_id=task_id)
    )

    intake = result["intake"]
    assert "backend-reelme-v2 has flaky retries" in intake["memory"]
    assert "read backend-reelme-v2's queue depth first" in intake["skills"]
    assert "read backend-reelme-v2's queue depth first" not in intake["memory"]


async def test_intake_of_round_trips_the_envelope_with_tuples_not_lists(db):
    from friday.sdk.intake import IntakeContext
    from plugins.backend.graph.intake import intake_node, intake_of
    from plugins.backend.placement import Placement

    await write_rows(db, env="dev", repo="/clone/reelme")
    task_id = await _task_with_text(
        db, f"service backend-reelme-v2 loi\n```\n{CURL}\n```", code=(CURL,)
    )

    result = await intake_node(core_intake).run(
        DAGState.empty(), deps_for(db, task_id=task_id)
    )
    context = intake_of(result)

    assert isinstance(context, IntakeContext)
    assert isinstance(context.domain, Placement)
    assert isinstance(context.domain.container_roots, tuple)
    assert isinstance(context.domain.dbs, tuple)
    assert isinstance(context.domain.candidates, tuple)
    assert isinstance(context.hints.uuids, tuple)
    assert isinstance(context.hints.artifacts, tuple)
    assert isinstance(context.memory, tuple)
    assert isinstance(context.skills, tuple)
    assert context.domain.service == "backend-reelme-v2"
    assert context.domain.curl_artifact_id == context.hints.artifacts[0].id


async def test_intake_result_survives_dagstate_storage_round_trip(db):
    """The property the `_listed` conversion exists for: the envelope must
    survive `DAGState.to_dict`'s `json.loads(json.dumps(v)) == v` check, or the
    result is dropped as `UNSTORABLE` and the node re-runs on resume. Guards the
    tuple->list pass — a raw tuple field would fail the equality and go red."""
    from friday.sdk.workflow_state import UNSTORABLE

    from plugins.backend.graph.intake import intake_node

    await write_rows(db, env="dev", repo="/clone/reelme")
    task_id = await _task_with_text(db, "service backend-reelme-v2 loi 500")

    result = await intake_node(core_intake).run(
        DAGState.empty(), deps_for(db, task_id=task_id)
    )
    stored = DAGState.empty().with_result("intake", result).to_dict()

    assert UNSTORABLE not in stored["intake"]
    assert stored["intake"] == result

