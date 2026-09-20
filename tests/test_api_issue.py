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

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from friday.dag.api_issue import build_api_issue_dag, build_log_sources
from friday.dag.api_issue.code import read_failing_code_node, repo_file
from friday.dag.api_issue.diagnose import (
    Diagnosis,
    diagnose_node,
    unresolved_refs,
)
from friday.dag.api_issue.logs import find_request_log_node
from friday.dag.api_issue.report import render, report_node
from friday.dag.api_issue.resolve import resolve_node
from friday.dag.engine import DAGDeps, DAGState, status_of
from friday.domain.actions import HandOver
from friday.domain.conversation import ConversationId
from friday.domain.models import ApiIssueParams, FridayState, MemoryKind, MemoryOrigin

CURL = (
    'curl -X POST -H "Content-Type: application/json" '
    '--data \'{"items":[]}\' '
    '"https://api-reelme-v2.dev.aperogroup.ai/v1/pod/orders/init"'
)
PROD_CURL = 'curl "https://api-reelme-v2.aperogroup.ai/v1/pod/orders/init"'


# --- fixtures ---------------------------------------------------------------


REPORTED_AT = datetime(2026, 9, 20, 4, 40, 46, tzinfo=timezone.utc)


def deps_for(db, *, task_id: int = 1, extra: dict | None = None) -> DAGDeps:
    return DAGDeps(
        task=SimpleNamespace(
            id=task_id,
            conversation=ConversationId("fake", "watched"),
            params={},
            created_at=REPORTED_AT,
        ),
        db=db,
        extra=extra or {},
    )


def prepared(**params) -> DAGState:
    return DAGState.empty().with_result(
        "prepare",
        ApiIssueParams(**{"summary": "500 khi init đơn", "curl": CURL, **params}),
    )


async def write_rows(db, *, env: str = "dev", repo: str | None = None):
    """The rows ticket 00 says are typed by hand: one route, one service, and
    the project the service belongs to."""
    state = FridayState(channel_id="watched", agent="admin")
    domain = (
        "api-reelme-v2.dev.aperogroup.ai" if env == "dev"
        else "api-reelme-v2.aperogroup.ai"
    )
    await db.memory_add(
        state, "ReelMe v2 on dev", kind=MemoryKind.ROUTE, origin=MemoryOrigin.ADMIN,
        data={"domain": domain, "env": env, "service": "backend-reelme-v2"},
    )
    await db.memory_add(
        state, "the ReelMe v2 backend", kind=MemoryKind.SERVICE,
        origin=MemoryOrigin.ADMIN,
        data={
            "name": "backend-reelme-v2",
            "project": "reelme",
            "prod": {"cluster": "vultr-ailab", "namespace": "sw", "app": "backend-reelme-v2"},
            "dev": {"kube_context": "dev", "namespace": "dev", "pod_pattern": "backend-reelme-v2"},
        },
    )
    if repo is not None:
        await db.memory_add(
            state, "the ReelMe repository", kind=MemoryKind.PROJECT,
            origin=MemoryOrigin.ADMIN,
            data={
                "name": "reelme", "repo_path": repo,
                "default_branch": "main", "stack": "NestJS",
            },
        )


@dataclass
class FakeSource:
    """A log source that answers from a script and records what it was asked."""

    answers: list[list[str]]
    name: str = "kubectl"
    asked: list[tuple] = None  # type: ignore[assignment]

    def __post_init__(self):
        self.asked = []

    async def lines(self, placement, *, since, until, limit: int) -> list[str]:
        self.asked.append((since, until))
        return self.answers[min(len(self.asked) - 1, len(self.answers) - 1)]

    @property
    def windows(self) -> list[str]:
        """Each read as "<hours>h" of lookback, for a readable assertion."""
        return [
            f"{round((until - since).total_seconds() / 3600, 2)}h"
            for since, until in self.asked
        ]


# --- resolve ----------------------------------------------------------------


async def test_the_environment_comes_from_the_domain_and_the_service_from_a_row(db):
    await write_rows(db)

    result = await resolve_node().run(prepared(), deps_for(db))

    assert status_of(result) == "ok"
    assert result["placement"]["env"] == "dev"
    assert result["placement"]["pod_pattern"] == "backend-reelme-v2"


async def test_production_reads_the_loki_labels_and_dev_reads_the_pod_pattern(db):
    await write_rows(db, env="production")

    result = await resolve_node().run(prepared(curl=PROD_CURL), deps_for(db))

    assert result["placement"]["cluster"] == "vultr-ailab"
    assert result["placement"]["app"] == "backend-reelme-v2"
    assert result["placement"]["pod_pattern"] == ""


async def test_a_domain_that_is_not_ours_ends_the_graph_at_once(db):
    """D1. Sometimes an unknown domain is a proxy of ours the table does not
    know yet, and that call is the operator's."""
    result = await resolve_node().run(
        prepared(curl='curl "https://api.stripe.com/v1/charges"'), deps_for(db)
    )

    assert isinstance(result, HandOver)
    assert "stripe.com" in result.reason


async def test_a_missing_route_row_is_a_hand_over_not_a_guess(db):
    """D3, and finding G: guessing which pod serves a domain is how a
    diagnosis gets built from another product's logs."""
    result = await resolve_node().run(prepared(), deps_for(db))

    assert isinstance(result, HandOver)
    assert "no route row" in result.reason


async def test_a_route_row_that_disagrees_with_the_domain_is_refused(db):
    """One of the two is wrong, and picking either silently is how a
    production search runs against dev."""
    state = FridayState(channel_id="watched", agent="admin")
    await db.memory_add(
        state, "mistyped", kind=MemoryKind.ROUTE, origin=MemoryOrigin.ADMIN,
        data={
            "domain": "api-reelme-v2.dev.aperogroup.ai",
            "env": "production",
            "service": "backend-reelme-v2",
        },
    )

    result = await resolve_node().run(prepared(), deps_for(db))

    assert isinstance(result, HandOver)
    assert "production" in result.reason and "dev" in result.reason


async def test_a_report_with_no_curl_says_so_rather_than_blaming_the_domain(db):
    """D2: without a curl there is no domain. A correlationId makes a report
    findable, not routable."""
    result = await resolve_node().run(
        prepared(curl=None, correlation_id="8f14e45f-ceea-467a-9b3a-1e0e4a1b2c3d"),
        deps_for(db),
    )

    assert isinstance(result, HandOver)
    assert "URL" in result.reason


# --- find_request_log -------------------------------------------------------


async def test_a_source_that_is_not_configured_skips_out_loud(db):
    """The failure the deleted five-node graph had: every node skipped on
    every run, and nothing said so."""
    await write_rows(db)
    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(state, deps_for(db))

    assert status_of(result) == "skipped"
    assert "kubectl" in result["reason"]


async def test_the_dossier_keeps_the_lines_that_name_the_request(db):
    await write_rows(db)
    source = FakeSource(answers=[[
        "GET /v1/health 200",
        "ERROR POST /v1/pod/orders/init 500 ERR19",
        "GET /v1/health 200",
    ]])
    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert status_of(result) == "ok"
    assert "ERR19" in result["dossier"]
    assert result["total"] == 3


async def test_a_quiet_window_is_widened_once_and_only_once(db):
    """Spec: one automatic widening of the window, recorded in `not_checked`."""
    await write_rows(db)
    source = FakeSource(answers=[["GET /v1/health 200"], ["GET /v1/health 200"]])
    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert source.windows == ["0.58h", "6.08h"]
    assert any("widened" in line for line in result["not_checked"])


async def test_the_window_is_measured_back_from_the_reporters_message(db):
    """D5: "within a window of six hours back from **the reporter's
    message**". A clock anchored to now finds nothing for a case reported
    this morning — and every one of ticket 00's five cases is old, so this is
    the difference between a slice that can be run against them and one that
    cannot."""
    await write_rows(db)
    source = FakeSource(answers=[["ERROR POST /v1/pod/orders/init 500"]])
    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    (since, until), = source.asked
    assert since == REPORTED_AT - timedelta(minutes=30)
    assert until == REPORTED_AT + timedelta(minutes=5)


async def test_a_source_that_fails_is_work_rather_than_a_crash(db):
    await write_rows(db)

    class Broken:
        name = "kubectl"

        async def lines(self, placement, *, since, until, limit):
            raise RuntimeError("ssh: Could not resolve hostname dev")

    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": Broken()}})
    )

    assert status_of(result) == "error"
    assert "Could not resolve hostname" in result["reason"]


# --- read_failing_code ------------------------------------------------------


def test_a_frame_is_mapped_into_the_clone(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "orders.ts").write_text("a\nb\nc\n")

    found = repo_file("/app/src/orders.ts", str(tmp_path))

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

    assert repo_file("/app/../secret.txt", str(repo)) is None
    assert repo_file("/etc/passwd", str(repo)) is None
    assert secret.is_file(), "the escape target was real"


async def test_the_code_node_says_it_read_head_rather_than_the_running_tag(
    db, tmp_path
):
    (tmp_path / "orders.ts").write_text("\n".join(f"line {i}" for i in range(50)))
    await write_rows(db, repo=str(tmp_path))
    resolve = await resolve_node().run(prepared(), deps_for(db))
    state = (
        prepared()
        .with_result("resolve", resolve)
        .with_result(
            "find_request_log",
            {"status": "ok", "reason": "", "frames": [["/app/orders.ts", 12]]},
        )
    )

    result = await read_failing_code_node().run(state, deps_for(db))

    assert status_of(result) == "ok"
    assert "line 11" in result["code"]
    assert any("HEAD" in line for line in result["not_checked"])


async def test_a_project_row_with_no_repository_path_reads_nothing(db):
    """`Path("")` is `.`, so an empty root turns "inside the clone" into
    "inside whatever directory this process is in" — and a frame from a log
    line then names files under it. Found by review, not by a run."""
    state = (
        prepared()
        .with_result("resolve", {
            "status": "ok", "reason": "",
            "placement": {"env": "dev", "service": "s"},
            "project": {"name": "reelme", "repo_path": ""},
        })
        .with_result("find_request_log", {
            "status": "ok", "reason": "",
            "frames": [["/app/conftest.py", 1]],
        })
    )

    result = await read_failing_code_node().run(state, deps_for(db))

    assert status_of(result) == "skipped"
    assert "no repository path" in result["reason"]


async def test_no_project_row_means_no_code_is_read(db):
    await write_rows(db)  # no project row
    resolve = await resolve_node().run(prepared(), deps_for(db))
    state = (
        prepared()
        .with_result("resolve", resolve)
        .with_result(
            "find_request_log",
            {"status": "ok", "reason": "", "frames": [["/app/orders.ts", 1]]},
        )
    )

    result = await read_failing_code_node().run(state, deps_for(db))

    assert status_of(result) == "skipped"


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

    class Inventing:
        last_error = None

        async def run_structured(self, prompt, **kw):
            return Diagnosis(
                cause="the database is down", confidence="certain",
                conclusive=True, refs=["L99"],
            )

    state = (
        prepared()
        .with_result(
            "find_request_log",
            {"status": "ok", "reason": "", "dossier": "ERROR ERR19", "not_checked": []},
        )
        .with_result("read_failing_code", {"status": "empty", "reason": "", "not_checked": []})
    )

    result = await diagnose_node(harness=Inventing()).run(state, deps_for(db))

    assert status_of(result) == "empty"
    assert "L99" in result["reason"]


async def test_a_grounded_diagnosis_is_carried_with_what_was_not_checked(db):
    class Honest:
        last_error = None

        async def run_structured(self, prompt, **kw):
            assert "L1 | ERROR ERR19" in prompt, "every line carries an id"
            return Diagnosis(
                cause="ERR19 từ orders.init", confidence="likely",
                conclusive=False, refs=["L1"],
            )

    state = (
        prepared()
        .with_result(
            "find_request_log",
            {
                "status": "ok", "reason": "", "dossier": "ERROR ERR19",
                "not_checked": ["the window was widened once"],
            },
        )
        .with_result(
            "read_failing_code",
            {"status": "ok", "reason": "", "code": "", "not_checked": ["read at HEAD"]},
        )
    )

    result = await diagnose_node(harness=Honest()).run(state, deps_for(db))

    assert status_of(result) == "ok"
    assert result["diagnosis"]["cause"] == "ERR19 từ orders.init"
    assert result["quotes"] == ["ERROR ERR19"], "code puts the text back"
    assert result["not_checked"] == ["the window was widened once", "read at HEAD"]


async def test_nothing_to_reason_over_is_not_something_to_reason_over(db):
    """A model asked to diagnose an empty dossier writes a plausible cause
    from the endpoint's name alone, and it reads like one built on evidence."""

    class Unused:
        last_error = None

        async def run_structured(self, prompt, **kw):  # pragma: no cover
            raise AssertionError("the model should not have been called")

    state = (
        prepared()
        .with_result("find_request_log", {"status": "skipped", "reason": "no source"})
        .with_result("read_failing_code", {"status": "empty", "reason": "no frame"})
    )

    result = await diagnose_node(harness=Unused()).run(state, deps_for(db))

    assert status_of(result) == "empty"


async def test_a_conclusive_answer_that_points_at_nothing_is_not_reported(db):
    """Otherwise the gate is optional: an answer with no pointers has nothing
    to refuse, and "conclusive" is exactly the claim that needs one."""

    class Assertive:
        last_error = None

        async def run_structured(self, prompt, **kw):
            return Diagnosis(
                cause="nó hỏng", confidence="certain", conclusive=True, refs=[],
            )

    state = (
        prepared()
        .with_result("find_request_log", {"status": "ok", "reason": "", "dossier": "ERROR x"})
        .with_result("read_failing_code", {"status": "empty", "reason": ""})
    )

    result = await diagnose_node(harness=Assertive()).run(state, deps_for(db))

    assert status_of(result) == "empty"
    assert "pointed at" in result["reason"]


async def test_the_first_frame_is_read_and_the_rest_are_named(db, tmp_path):
    """The spec reads "±15 lines around the first frame"; the others are on
    the stack, not at the throw site. `node_modules` frames are dropped
    **before** the cap — truncating first buried the throw site under five
    framework frames, which is the shape a NestJS trace actually has."""
    (tmp_path / "orders.ts").write_text("\n".join(f"line {i}" for i in range(40)))
    await write_rows(db, repo=str(tmp_path))
    resolve = await resolve_node().run(prepared(), deps_for(db))
    state = (
        prepared()
        .with_result("resolve", resolve)
        .with_result("find_request_log", {
            "status": "ok", "reason": "",
            "frames": [
                ["/app/node_modules/express/lib/router.js", 3],
                ["/app/orders.ts", 12],
                ["/app/orders.ts", 30],
            ],
        })
    )

    result = await read_failing_code_node().run(state, deps_for(db))

    assert "line 11" in result["code"], "the first frame that is ours"
    assert "line 29" not in result["code"], "the rest are named, not opened"
    assert any("further down the stack" in line for line in result["not_checked"])
    assert not any("node_modules" in line for line in result["not_checked"])


async def test_no_diagnose_agent_skips_rather_than_failing(db):
    state = (
        prepared()
        .with_result("find_request_log", {"status": "ok", "reason": "", "dossier": "x"})
        .with_result("read_failing_code", {"status": "empty", "reason": ""})
    )

    result = await diagnose_node(harness=None).run(state, deps_for(db))

    assert status_of(result) == "skipped"


# --- report -----------------------------------------------------------------


async def test_the_report_is_written_and_the_task_is_handed_over(db, tmp_path):
    state = (
        prepared()
        .with_result("resolve", {"status": "ok", "reason": "", "placement": {
            "env": "dev", "service": "backend-reelme-v2", "pod_pattern": "p",
        }, "project": None})
        .with_result(
            "find_request_log",
            {"status": "ok", "reason": "", "dossier": "ERROR ERR19", "source": "kubectl",
             "kept": 1, "total": 90, "not_checked": []},
        )
        .with_result("read_failing_code", {"status": "empty", "reason": "no frame"})
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
    assert isinstance(result, HandOver)
    assert "Nothing has been sent to anyone" in result.reason
    assert "ERR19" in written.read_text()
    assert "read at HEAD" in written.read_text()


def test_the_report_says_what_it_did_not_check_even_with_no_diagnosis():
    from datetime import datetime, timezone

    state = (
        DAGState.empty()
        .with_result("find_request_log", {"status": "skipped", "reason": "no kubectl source"})
        .with_result("read_failing_code", {"status": "skipped", "reason": "no project row"})
    )

    text = render(state, task_id=6, at=datetime(2026, 9, 20, tzinfo=timezone.utc))

    assert "no kubectl source" in text
    assert "no project row" in text


# --- the graph --------------------------------------------------------------


def test_api_issue_is_the_one_graph_with_an_investigation_past_node_zero():
    dag = build_api_issue_dag()

    assert [n.name for n in dag.nodes] == [
        "prepare", "resolve", "find_request_log", "read_failing_code",
        "diagnose", "report",
    ]


def test_nothing_past_resolve_runs_when_resolve_hands_over():
    """A node reading an earlier node's hand-over as if it were a result is
    the wiring mistake `DAGState` raises on, and the fix is in the edges."""
    dag = build_api_issue_dag()
    state = DAGState.empty().with_result("resolve", HandOver("not ours"))

    assert dag.next_after("resolve", state) is None


def test_a_skipped_log_node_still_reaches_the_report():
    dag = build_api_issue_dag()
    state = DAGState.empty().with_result(
        "find_request_log", {"status": "skipped", "reason": "no source"}
    )

    assert dag.next_after("find_request_log", state) == "read_failing_code"


def test_a_blank_setting_means_not_configured_rather_than_the_word_none():
    """`ssh_host:` with nothing after it parses as `None`, and `str(None)` is
    a truthy `"None"` — which built a source pointing at a host called None
    and turned every dev task into a failed subprocess."""
    from friday.config import _api_issue

    assert _api_issue({"ssh_host": None}).ssh_host == ""
    assert build_log_sources(
        SimpleNamespace(api_issue=_api_issue({"ssh_host": None})), {}
    ) == {}


def test_log_sources_are_only_built_for_what_is_configured():
    from friday.config import ApiIssueConfig

    none = build_log_sources(SimpleNamespace(api_issue=ApiIssueConfig()), {})
    dev = build_log_sources(
        SimpleNamespace(api_issue=ApiIssueConfig(ssh_host="dev")), {}
    )
    prod = build_log_sources(
        SimpleNamespace(api_issue=ApiIssueConfig(loki_server="devops")),
        {"devops": object()},
    )

    assert none == {}
    assert set(dev) == {"kubectl"}
    assert set(prod) == {"loki"}


async def test_a_complete_report_is_investigated_rather_than_handed_back(db):
    """The behaviour task 6 met, and the reason this board exists.

    Node 0 finds nothing to ask about — a curl satisfies `_traceable` — and
    before this graph the answer was "everything needed is here, and there is
    no investigation past this point". Now the same task reaches `Resolve`,
    and what it hands over with is a missing *row*, which somebody can fix.
    """
    from friday.tasks.pool import Pool
    from tests.test_pool import make_task

    await make_task(db, curl=CURL, environment="dev")

    await Pool(db=db, auto_ask=True).run_once()

    _, question = await db.dag_pause((await db.tasks())[0].id)
    assert "no investigation past this point" not in question
    assert "route row" in question


async def test_the_whole_line_runs_from_a_curl_to_a_report(db, tmp_path):
    """Every node of the slice, in one pass, with the rows and a source in
    place — the shape ticket 00's five cases are run through."""
    from friday.dag.engine import DAGRunner
    from tests.test_pool import make_task

    (tmp_path / "orders.ts").write_text("\n".join(f"line {i}" for i in range(40)))
    await write_rows(db, repo=str(tmp_path))
    task = await make_task(db, curl=CURL, environment="dev")

    class Honest:
        last_error = None

        async def run_structured(self, prompt, **kw):
            assert "ERR19" in prompt, "the dossier reaches the model"
            return Diagnosis(
                cause="ERR19 ở orders.init", confidence="likely",
                conclusive=False, refs=["L1"],
            )

    reports_dir = tmp_path / "reports"
    dag = build_api_issue_dag(
        diagnose_harness=Honest(), reports_dir=reports_dir
    )
    source = FakeSource(answers=[[
        "ERROR ERR19 POST /v1/pod/orders/init 500",
        "at handler (/app/orders.ts:12:3)",
    ]])
    state = DAGState.empty().with_result(
        "prepare", ApiIssueParams(**task.params)
    )
    runner = DAGRunner(
        dag,
        deps=DAGDeps(
            task=task, db=db, extra={"log_sources": {"kubectl": source}}
        ),
        state=state,
    )

    final = await runner.run()

    # No `prepare`: node 0 runs outside the walk and its result is handed to
    # the runner already recorded, so the trail starts where the walk does.
    # `Pool._run_dag` does exactly this on a fresh pass.
    assert runner.trail == [
        "resolve", "find_request_log", "read_failing_code", "diagnose", "report",
    ]
    assert isinstance(final["report"], HandOver)
    (written,) = list(reports_dir.glob("*.md"))
    text = written.read_text()
    assert "ERR19 ở orders.init" in text
    assert "line 11" in text, "the source around the frame is in the report"
    assert "HEAD" in text, "and what it did not check"
