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

import pytest

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


async def write_environment_rows(db, channel_id: str = "watched"):
    """What a domain suffix means. The convention as two rows, longest-suffix
    wins — which is also how an exception is written (2026-09-21, amending
    D1)."""
    state = FridayState(channel_id=channel_id, agent="admin")
    for suffix, env in (("aperogroup.ai", "production"), ("dev.aperogroup.ai", "dev")):
        await db.memory_add(
            state, f"{suffix} is {env}", kind=MemoryKind.ENVIRONMENT,
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
        state, "the ReelMe repository", kind=MemoryKind.PROJECT,
        origin=MemoryOrigin.ADMIN,
        data={
            "name": "reelme", "repo_path": repo or "/nowhere",
            "default_branch": "main", "stack": "NestJS",
        },
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
    await db.memory_add(
        state, "ReelMe v2 on dev", kind=MemoryKind.ROUTE, origin=MemoryOrigin.ADMIN,
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

    #: What the pod's oldest line is, when the fake is standing in for a
    #: back end that can say. `None` is one that cannot, like Loki.
    oldest: object = None
    truncated: bool = False

    async def lines(self, placement, *, since, until, limit: int):
        from friday.sources import Lines

        self.asked.append((since, until))
        answer = self.answers[min(len(self.asked) - 1, len(self.answers) - 1)]
        return Lines(tuple(answer), self.oldest, self.truncated)

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
    """Sometimes an unknown domain is a proxy of ours the table does not know
    yet, and that call is the operator's."""
    await write_environment_rows(db)

    result = await resolve_node().run(
        prepared(curl='curl "https://api.stripe.com/v1/charges"'), deps_for(db)
    )

    assert isinstance(result, HandOver)
    assert "stripe.com" in result.reason


async def test_a_missing_route_row_is_a_hand_over_not_a_guess(db):
    """D3, and finding G: guessing which pod serves a domain is how a
    diagnosis gets built from another product's logs."""
    await write_environment_rows(db)

    result = await resolve_node().run(prepared(), deps_for(db))

    assert isinstance(result, HandOver)
    assert "no route row" in result.reason


async def test_a_route_row_that_disagrees_with_the_domain_is_refused(db):
    """One of the two is wrong, and picking either silently is how a
    production search runs against dev."""
    # The service first: ticket 19 refuses a route naming one that does not
    # exist, so the row this test is about can only be written after it.
    await write_rows(db)
    state = FridayState(channel_id="watched", agent="admin")
    await db.memory_delete(
        state,
        [m for m in await db.memories_for_channel("watched")
         if m.kind == MemoryKind.ROUTE][0].id,
        origin=MemoryOrigin.ADMIN,
    )
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


async def test_a_room_that_knows_no_domains_says_that_and_not_external(db):
    """A room with no `environment` rows knows nothing about any domain.
    Answering "not ours" there would be a claim rather than a lookup, and it
    is the claim an operator would act on by looking somewhere else."""
    result = await resolve_node().run(prepared(), deps_for(db))

    assert isinstance(result, HandOver)
    assert "which domains are ours" in result.reason


async def test_one_row_for_one_host_beats_the_convention_it_breaks(db):
    """The 2026-09-18 survey found `api-mobile-spec-reviewer.aperogroup.ai`
    served from `dev` with no `.dev` in it. Longest suffix wins, so the
    exception is one more row rather than a branch."""
    from friday.dag.api_issue.resolve import environment_of

    rows = [
        SimpleNamespace(suffix="aperogroup.ai", env="production"),
        SimpleNamespace(suffix="dev.aperogroup.ai", env="dev"),
        SimpleNamespace(suffix="api-mobile-spec-reviewer.aperogroup.ai", env="dev"),
    ]

    assert environment_of("api-reelme-v2.aperogroup.ai", rows) == "production"
    assert environment_of("api-reelme-v2.dev.aperogroup.ai", rows) == "dev"
    assert environment_of("api-mobile-spec-reviewer.aperogroup.ai", rows) == "dev"
    assert environment_of("api.stripe.com", rows) == "external"


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


async def test_a_window_older_than_the_pod_keeps_is_not_reported_as_not_found(db):
    """Found by the first real run, 2026-09-21. Case 1 was reported sixteen
    hours before the oldest line its dev pod still held. "Nothing names this
    request" would send the operator looking for a request that was never
    searched for; it was not there to search."""
    await write_rows(db)
    source = FakeSource(answers=[[]], oldest=REPORTED_AT + timedelta(hours=16))
    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert status_of(result) == "empty"
    assert "oldest line" in result["reason"]
    assert "not searched for" in result["reason"]
    assert any("does not reach back" in line for line in result["not_checked"])


def test_the_kubectl_window_is_clipped_by_the_runtimes_own_stamps():
    """`--tail` counts from the newest line and there is no `--until`, so a
    window that opened sixteen hours ago came back as the newest 400 lines of
    today — and the node reported them as the reporter's. `--timestamps` is
    what makes the upper bound possible, and it is the container runtime's
    stamp rather than the line's own field, because dev is JSON today and a
    Python traceback tomorrow."""
    from friday.sources.logs import _within

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
    """**Not reachable through the store any more, and still reachable.**
    Ticket 19 refuses a `service` naming a `project` that does not exist and
    refuses removing one that is still named — so this state cannot be
    *typed*. It can still be *met*: the live database holds rows written
    before that check, and this node is what reads them. So the envelope is
    built here rather than through `write_rows`, which can no longer produce
    it."""
    state = (
        prepared()
        .with_result("resolve", {
            "status": "ok", "reason": "",
            "placement": {"env": "dev", "service": "backend-reelme-v2"},
            "project": None,
        })
        .with_result(
            "find_request_log",
            {"status": "ok", "reason": "", "frames": [["/app/orders.ts", 1]]},
        )
    )

    result = await read_failing_code_node().run(state, deps_for(db))

    assert status_of(result) == "skipped"
    # Ticket 19, step 0: the message names what it looked for. Without it,
    # the first six rows ever typed gave "no project row names a repository"
    # and no way to tell which name was wrong.
    assert "backend-reelme-v2" in result["reason"]


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

    await write_environment_rows(db)
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


# --- replay_case.py, the tool that answers questions 3 and 4 -----------------


def test_a_node_that_ended_the_run_does_not_read_as_one_that_passed_it_on():
    """`node_runs` records any `Action` as `ok`, because an `Action` carries
    no envelope. So a `resolve` that handed over — ending the whole run —
    printed exactly like a `resolve` that succeeded, and the first real use
    of this tool was ten minutes of reading the wrong thing."""
    from friday.dag.engine import NodeRun
    from replay_case import answers

    run = NodeRun(
        dag_name="api_issue", dag_version="v", node="resolve", attempt=1,
        status="ok", reason="", duration_ms=6,
    )
    final = DAGState.empty().with_result("resolve", HandOver("no service row"))

    found = answers([run], final, wall_s=0.1)

    assert found["nodes"][0]["status"] == "handed over"
    assert found["nodes"][0]["reason"] == "no service row"


def test_a_node_that_failed_is_named_as_a_seam_that_broke():
    """Question 4. `empty` and `skipped` are nodes doing their job with
    nothing to work on; `error` and `timed_out` are the seams."""
    from friday.dag.engine import NodeRun
    from replay_case import answers

    def run(node, status, reason=""):
        return NodeRun(
            dag_name="api_issue", dag_version="v", node=node, attempt=1,
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
    from friday.sources.logs import _streams

    found = _streams(LOKI_ANSWER)

    assert found.lines[0].endswith('/v1/workflow"}')
    assert found.lines[-1].endswith('/v2/daily-shot"}')
    assert found.oldest.isoformat().startswith("2026-09-21T08:27:49.163")


def test_the_timestamp_comes_off_and_the_cap_is_reported():
    from friday.sources.logs import _streams

    found = _streams(LOKI_ANSWER)

    assert not any(line.startswith("2026-") for line in found.lines)
    assert found.truncated is True


def test_an_answer_loki_never_gave_is_empty_rather_than_a_crash():
    """A changed shape should read as "Loki said nothing I understood",
    which the node reports, not as a graph that died."""
    from friday.sources.logs import _streams

    assert _streams("not json").lines == ()
    assert _streams('{"streams": null}').lines == ()


async def test_a_capped_answer_says_so_in_what_it_did_not_check(db):
    await write_rows(db)
    source = FakeSource(answers=[["ERROR POST /v1/pod/orders/init 500"]])
    source.truncated = True
    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert any("a sample of the window" in line for line in result["not_checked"])


async def test_a_reader_may_not_call_a_tool_it_did_not_declare():
    """The operator's call, 2026-09-21: which tools may be called is code,
    not configuration. The server this narrows also offers `release_apply`,
    `release_rollback` and `godaddy_dns_edit_record` — and a guard a file can
    widen is one the file's next editor widens by accident."""
    from friday.sources import Reads
    from friday.sources.logs import LokiSource

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
    from friday.sources import Reads

    assert hasattr(Reads, "call") and not hasattr(Reads, "call_tool")


def test_what_a_server_is_filtered_to_is_read_off_the_readers():
    """A list beside the classes is a list that disagrees with them — so the
    set grows when a reader is added and by no other means."""
    from friday.sources import declared
    from friday.sources.db import DbSource
    from friday.sources.logs import LokiSource

    assert declared() == LokiSource.TOOLS | DbSource.TOOLS
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
    from friday.sources.code import original

    _built(tmp_path, js_line=3, ts_line=11)

    found = original(tmp_path / "dist" / "x.js", 3, tmp_path)

    assert found == ((tmp_path / "src" / "x.ts").resolve(), 11)


def test_a_compiled_file_with_no_map_beside_it_is_not_translated(tmp_path):
    """`None` means "read the built file and say so", never "guess"."""
    from friday.sources.code import original

    _built(tmp_path)
    (tmp_path / "dist" / "x.js.map").unlink()

    assert original(tmp_path / "dist" / "x.js", 3, tmp_path) is None


def test_a_map_that_does_not_parse_is_not_translated(tmp_path):
    from friday.sources.code import original

    _built(tmp_path)
    (tmp_path / "dist" / "x.js.map").write_text("{ not json")

    assert original(tmp_path / "dist" / "x.js", 3, tmp_path) is None


async def test_the_node_reads_the_typescript_and_says_where_it_came_from(db, tmp_path):
    _built(tmp_path, js_line=3, ts_line=11)
    state = (
        prepared()
        .with_result("resolve", {
            "status": "ok", "reason": "",
            "placement": {"env": "dev", "service": "backend-reelme-v2"},
            "project": {"name": "reelme", "repo_path": str(tmp_path)},
        })
        .with_result("find_request_log", {
            "status": "ok", "reason": "", "frames": [["/app/dist/x.js", 3]],
        })
    )

    result = await read_failing_code_node().run(state, deps_for(db))

    assert "ts line 11" in result["code"], "the source, at the mapped line"
    assert "js line 3" not in result["code"]
    assert "x.ts:11" in result["code"] and "/app/dist/x.js" in result["code"]


async def test_a_built_frame_with_no_map_says_it_is_the_built_line(db, tmp_path):
    _built(tmp_path)
    (tmp_path / "dist" / "x.js.map").unlink()
    state = (
        prepared()
        .with_result("resolve", {
            "status": "ok", "reason": "",
            "placement": {"env": "dev", "service": "s"},
            "project": {"name": "reelme", "repo_path": str(tmp_path)},
        })
        .with_result("find_request_log", {
            "status": "ok", "reason": "", "frames": [["/app/dist/x.js", 3]],
        })
    )

    result = await read_failing_code_node().run(state, deps_for(db))

    assert "js line 3" in result["code"]
    assert any("not the one you wrote" in line for line in result["not_checked"])


def test_a_map_naming_a_file_outside_the_clone_is_refused(tmp_path):
    """`repo_file` refuses a stack frame that climbs out of the clone, and a
    `.map` naming `../../../../etc/hosts` is the same climb by a quieter
    route. The rule was written at the top of that module and then not
    applied to the function added under it — found by review."""
    import json

    from friday.sources.code import original

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

    from friday.sources.code import original

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

    from friday.sources.code import original

    _built(tmp_path, js_line=3, ts_line=11)
    map_file = tmp_path / "dist" / "x.js.map"
    loaded = json.loads(map_file.read_text())
    # line 1: a whole segment jumping +40. line 2: the same, corrupted.
    # line 3: +1 from wherever the carry left off.
    loaded["mappings"] = "AACA;AA!QA;AACA".replace("!", "!")
    map_file.write_text(json.dumps(loaded))

    assert original(tmp_path / "dist" / "x.js", 3, tmp_path) is None
