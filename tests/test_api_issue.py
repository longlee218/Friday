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

from plugins.devops.graph import build_devops_dag, build_log_sources
from plugins.devops.config import DevopsConfig, load_devops_config
from plugins.devops.config import DEFAULT_CONTAINER_ROOTS
from plugins.devops.graph.code import read_failing_code_node, repo_file
from plugins.devops.graph.diagnose import (
    Diagnosis,
    diagnose_node,
    unresolved_refs,
)
from plugins.devops.graph.logs import find_request_log_node
from plugins.devops.graph.report import render, report_node


class _StubCaps:
    """A stand-in for the composition root's boot caps, for building the devops
    DAG in a test without a live process (ticket 14)."""

    def __init__(self, *, diagnose_harness=None, budget_tokens=None, diagnose_agent=None):
        self.config = SimpleNamespace(
            agents=({"devops.diagnose": diagnose_agent} if diagnose_agent else {}),
            context=SimpleNamespace(extraction_budget_tokens=budget_tokens),
        )
        self.servers = {}
        self.sender = "discord_user"
        self.approver = "discord_bot"
        self._harness = diagnose_harness

    def prepare_node(self, *a, **k):
        from friday.kernel.dag.prepare import prepare_node
        return prepare_node(*a, **k)

    def make_harness(self, *, agent, instructions, answers=None, tools=None):
        return self._harness


def _dag(*, diagnose_harness=None, reports_dir=None, budget_tokens=None, diagnose_agent=None):
    """The `devops.api_issue` DAG, built through the plugin's own builder."""
    api = SimpleNamespace(
        caps=_StubCaps(
            diagnose_harness=diagnose_harness,
            budget_tokens=budget_tokens,
            diagnose_agent=diagnose_agent,
        ),
        config=DevopsConfig(reports_dir=str(reports_dir) if reports_dir else "./data/reports"),
    )
    return build_devops_dag(api)

from plugins.devops.graph.resolve import resolve_node
from plugins.devops.graph.deps import ApiIssueDeps
from friday.sdk.workflow import DAGState, status_of
from friday.sdk.actions import Ask, HandOver, Reply
from friday.kernel.domain.conversation import ConversationId
from friday.sdk.memory import MemoryOrigin
from friday.kernel.domain.models import FridayState
from plugins.devops.params import ApiIssueParams

CURL = (
    'curl -X POST -H "Content-Type: application/json" '
    '--data \'{"items":[]}\' '
    '"https://api-reelme-v2.dev.aperogroup.ai/v1/pod/orders/init"'
)
PROD_CURL = 'curl "https://api-reelme-v2.aperogroup.ai/v1/pod/orders/init"'


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
            state, f"{suffix} is {env}", kind="devops.environment",
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
        state, "the ReelMe repository", kind="devops.project",
        origin=MemoryOrigin.ADMIN,
        data={
            "name": "reelme", "repo_path": repo or "/nowhere",
            "default_branch": "main", "stack": "NestJS",
        },
    )
    await db.memory_add(
        state, "the ReelMe v2 backend", kind="devops.service",
        origin=MemoryOrigin.ADMIN,
        data={
            "name": "backend-reelme-v2",
            "project": "reelme",
            "prod": {"cluster": "vultr-ailab", "namespace": "sw", "app": "backend-reelme-v2"},
            "dev": {"kube_context": "dev", "namespace": "dev", "pod_pattern": "backend-reelme-v2"},
        },
    )
    await db.memory_add(
        state, "ReelMe v2 on dev", kind="devops.route", origin=MemoryOrigin.ADMIN,
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
         if m.kind == "devops.route"][0].id,
        origin=MemoryOrigin.ADMIN,
    )
    await db.memory_add(
        state, "mistyped", kind="devops.route", origin=MemoryOrigin.ADMIN,
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
    from plugins.devops.graph.resolve import environment_of

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


async def test_the_identifier_narrows_the_read_when_there_is_no_correlation_id(db):
    """D2's second way of naming a request, now that the type carries it
    (board `read-it-the-way-the-operator-does`, ticket 01).

    The identifier and not the endpoint path, which is the whole point of
    asking for one: the path matches every caller of it, and `limit` is a
    tail — measured on the production case of 2026-09-21, narrowing on the
    path returned 18 lines of which ten were other people's successful
    requests. The path stays in what `distil` matches on; it is only the
    *narrowed read* this chooses.
    """
    await write_rows(db)
    source = FakeSource(answers=[[
        "GET /v1/health 200",
        "ERROR POST /v1/pod/orders/init 500 ERR19 device-42",
    ]])
    state = prepared(correlation_id=None, identifier="device-42")

    await find_request_log_node().run(
        state.with_result("resolve", await resolve_node().run(state, deps_for(db))),
        deps_for(db, extra={"log_sources": {"kubectl": source}}),
    )

    assert [needle for _, _, needle in source.asked if needle] == ["device-42"]


async def test_a_quiet_window_is_widened_once_and_only_once(db):
    """Spec: one automatic widening of the window, recorded in `not_checked`."""
    await write_rows(db)
    source = FakeSource(answers=[["GET /v1/health 200"], ["GET /v1/health 200"]])
    # A reporter who has already given everything they have, so the empty
    # window is reported rather than asked about — the widening is what this
    # test is for.
    said = prepared(response="artifact-1")
    state = said.with_result(
        "resolve", await resolve_node().run(said, deps_for(db))
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

    # Both reads of the one window — the window itself and the one narrowed
    # to this request — and both anchored to the message, not to now.
    assert {(since, until) for since, until, _ in source.asked} == {
        (REPORTED_AT - timedelta(minutes=30), REPORTED_AT + timedelta(minutes=5))
    }


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
    from plugins.devops.sources.logs import _within

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
                # Filled, so this reaches the refs gate rather than the
                # alternatives one: the two refuse for different reasons and
                # this test is about pointing at nothing.
                alternatives_rejected=[
                    {"hypothesis": "mạng chập", "why": "không có timeout nào"}
                ],
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


async def test_the_report_is_written_and_the_reporter_is_offered_the_cause(db, tmp_path):
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
    assert isinstance(result, Reply)
    assert "ERR19" in result.text
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


# --- three outputs, one of them approved (ticket 06) ------------------------


async def test_the_reporter_is_told_it_is_being_worked_on(db):
    """Seconds of log reading, a clone and a model call. A reporter told
    nothing in that time does not know anything is happening, and what people
    do about silence is ask again."""
    from conftest import make_event

    from plugins.devops.graph.acknowledge import SAYS, acknowledge_node
    from friday.kernel.outbox import DEFAULT_SENDER, Kind

    await db.record_message(make_event(message_id="m1"))
    result = await acknowledge_node().run(
        prepared(), deps_for(db, extra={"sender": DEFAULT_SENDER})
    )

    assert status_of(result) == "ok"
    (row,) = await db.outbound()
    assert row.kind == Kind.ACKNOWLEDGED
    assert row.text == SAYS
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
    from plugins.devops.graph.acknowledge import acknowledge_node
    from friday.kernel.outbox import DEFAULT_SENDER, Kind

    await acknowledge_node().run(
        prepared(), deps_for(db, extra={"sender": DEFAULT_SENDER})
    )

    assert not Kind.ACKNOWLEDGED.needs_approval
    assert [r.kind for r in await db.sendable_outbound()] == [Kind.ACKNOWLEDGED]


async def test_a_resumed_graph_does_not_acknowledge_twice(db):
    """A graph re-runs from its checkpoint after the reporter answers a
    question. A second "đang xử lý" three minutes after the first reads as a
    stuck robot."""
    from plugins.devops.graph.acknowledge import acknowledge_node
    from friday.kernel.outbox import DEFAULT_SENDER

    deps = deps_for(db, extra={"sender": DEFAULT_SENDER})
    await acknowledge_node().run(prepared(), deps)
    again = await acknowledge_node().run(prepared(), deps)

    assert status_of(again) == "skipped"
    assert len(await db.outbound()) == 1


def test_nobody_is_acknowledged_before_the_placement_is_known():
    """An external domain, an unknown route or a service with no row all end
    the run at `resolve`. None of them should have told anybody that work was
    starting.

    Asserts *which node* `resolve` leads to, not merely that a hand-over
    stops the walk — the first version of this test asserted the latter,
    which is a different test that already existed, and stayed green with
    `acknowledge` moved ahead of `resolve`.
    """
    dag = _dag()
    ok = DAGState.empty().with_result("resolve", {"status": "ok", "reason": ""})

    assert dag.next_after("resolve", ok) == "acknowledge"
    assert dag.next_after(
        "resolve", DAGState.empty().with_result("resolve", HandOver("not ours"))
    ) is None
    assert [n.name for n in dag.nodes].index("acknowledge") > \
        [n.name for n in dag.nodes].index("resolve")


async def test_a_run_with_no_sender_investigates_anyway(db):
    """Nothing here is worth failing an investigation over — and a run that
    says why it stayed quiet beats one that is quiet about being quiet."""
    from plugins.devops.graph.acknowledge import acknowledge_node

    result = await acknowledge_node().run(prepared(), deps_for(db))

    assert status_of(result) == "skipped"
    assert "sender" in result["reason"]
    assert _dag().next_after(
        "acknowledge", DAGState.empty().with_result("acknowledge", result)
    ) == "find_request_log"


def _diagnosed() -> DAGState:
    """A state that reached a cause, which is what the last two outputs need."""
    return prepared().with_result("diagnose", {
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
    from plugins.devops.graph.report import brief

    said = brief(Diagnosis(
        cause="categoryId rỗng", confidence="certain", conclusive=True,
        refs=["L1"], next_checks=["hỏi BE về mapping"],
    ))

    assert said == "categoryId rỗng"


def test_a_brief_that_is_not_conclusive_says_so_in_words():
    from plugins.devops.graph.report import brief

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
        "prepare", "resolve", "acknowledge", "find_request_log",
        "read_failing_code", "diagnose", "report",
    ]


def test_nothing_past_resolve_runs_when_resolve_hands_over():
    """A node reading an earlier node's hand-over as if it were a result is
    the wiring mistake `DAGState` raises on, and the fix is in the edges."""
    dag = _dag()
    state = DAGState.empty().with_result("resolve", HandOver("not ours"))

    assert dag.next_after("resolve", state) is None


def test_a_skipped_log_node_still_reaches_the_report():
    dag = _dag()
    state = DAGState.empty().with_result(
        "find_request_log", {"status": "skipped", "reason": "no source"}
    )

    assert dag.next_after("find_request_log", state) == "read_failing_code"


def test_a_blank_setting_means_not_configured_rather_than_the_word_none():
    """`ssh_host:` with nothing after it parses as `None`, and `str(None)` is
    a truthy `"None"` — which built a source pointing at a host called None
    and turned every dev task into a failed subprocess."""
    
    assert load_devops_config({"ssh_host": None}).ssh_host == ""
    assert build_log_sources(
        load_devops_config({"ssh_host": None}), {}
    ) == {}


def test_log_sources_are_only_built_for_what_is_configured():
    
    none = build_log_sources(DevopsConfig(), {})
    dev = build_log_sources(
        DevopsConfig(ssh_host="dev"), {}
    )
    prod = build_log_sources(
        DevopsConfig(loki_server="devops"), {"devops": object()},
    )

    assert none == {}
    assert set(dev) == {"kubectl"}
    assert set(prod) == {"loki"}


async def test_a_complete_report_is_investigated_rather_than_handed_back(db, workflows):
    """The behaviour task 6 met, and the reason this board exists.

    Node 0 finds nothing to ask about — a curl satisfies `_traceable` — and
    before this graph the answer was "everything needed is here, and there is
    no investigation past this point". Now the same task reaches `Resolve`,
    and what it hands over with is a missing *row*, which somebody can fix.
    """
    from friday.kernel.pool.pool import Pool
    from tests.test_pool import make_task

    await write_environment_rows(db)
    await make_task(db, curl=CURL, environment="dev")

    await Pool(db=db, auto_ask=True).run_once()

    pauses = await db.pauses_for([(await db.tasks())[0].id])
    (question,) = pauses.values()
    assert "no investigation past this point" not in question
    assert "route row" in question


async def test_the_whole_line_runs_from_a_curl_to_a_report(db, tmp_path):
    """Every node of the slice, in one pass, with the rows and a source in
    place — the shape ticket 00's five cases are run through."""
    from replay_case import _run_on_adapter
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
    dag = _dag(
        diagnose_harness=Honest(), reports_dir=reports_dir
    )
    source = FakeSource(answers=[[
        "ERROR ERR19 POST /v1/pod/orders/init 500",
        "at handler (/app/orders.ts:12:3)",
    ]])
    # Node 0 runs outside the walk (the pool does exactly this); the workflow
    # runs from `resolve` with `prepare` pre-seeded.
    final, runs, _ = await _run_on_adapter(
        dag,
        deps=ApiIssueDeps(task=task, db=db, sender="", approver="", log_sources={"kubectl": source}),
        seed={"prepare": ApiIssueParams(**task.params)},
        system_db=tmp_path / "sys.db",
        wfid="test-whole-line",
    )

    assert [r.node for r in runs] == [
        "resolve", "acknowledge", "find_request_log", "read_failing_code",
        "diagnose", "report",
    ]
    # A cause was reached, so the reporter is offered one — and it waits for
    # approval, which is what `Reply` means to the pool.
    assert isinstance(final["report"], Reply)
    assert "ERR19 ở orders.init" in final["report"].text
    (written,) = list(reports_dir.glob("*.md"))
    text = written.read_text()
    assert "ERR19 ở orders.init" in text
    assert "line 11" in text, "the source around the frame is in the report"
    assert "HEAD" in text, "and what it did not check"


# --- replay_case.py, the tool that answers questions 3 and 4 -----------------


def test_a_captured_case_runs_through_the_real_source():
    """The point of capturing an answer rather than a list of lines: the
    parsing, the stamps, the stream merge and the `truncated` flag are the
    real ones, and only the socket is missing. A capture that stored lines
    would test the fake's own parsing and pass while `_streams` was broken.
    """
    import asyncio

    from friday.sdk.sources import Placement
    from plugins.devops.sources.logs import LokiSource
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

    from plugins.devops.params import ApiIssueParams
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

    from friday.sdk.sources import Placement
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


def test_a_node_that_ended_the_run_does_not_read_as_one_that_passed_it_on():
    """`node_runs` records any `Action` as `ok`, because an `Action` carries
    no envelope. So a `resolve` that handed over — ending the whole run —
    printed exactly like a `resolve` that succeeded, and the first real use
    of this tool was ten minutes of reading the wrong thing."""
    from friday.sdk.workflow import NodeRun
    from replay_case import answers

    run = NodeRun(
        dag_name="devops.api_issue", node="resolve", attempt=1,
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
            dag_name="devops.api_issue", node=node, attempt=1,
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
    from plugins.devops.sources.logs import _streams

    found = _streams(LOKI_ANSWER)

    assert found.lines[0].endswith('/v1/workflow"}')
    assert found.lines[-1].endswith('/v2/daily-shot"}')
    assert found.oldest.isoformat().startswith("2026-09-21T08:27:49.163")


def test_the_timestamp_comes_off_and_the_cap_is_reported():
    from plugins.devops.sources.logs import _streams

    found = _streams(LOKI_ANSWER)

    assert not any(line.startswith("2026-") for line in found.lines)
    assert found.truncated is True


def test_an_answer_loki_never_gave_is_empty_rather_than_a_crash():
    """A changed shape should read as "Loki said nothing I understood",
    which the node reports, not as a graph that died."""
    from plugins.devops.sources.logs import _streams

    assert _streams("not json").lines == ()
    assert _streams('{"streams": null}').lines == ()


async def test_a_capped_answer_says_which_part_of_the_window_it_covered(db):
    """Measured against production, 2026-09-21. The sentence used to say "a
    sample of the window and not all of it" and stop — and a dossier drawn
    from the last eighty-four seconds of a thirty-five minute window reads
    exactly like a dossier of the whole of it. The span is the difference
    between the two, so the span is what it has to say."""
    await write_rows(db)
    source = FakeSource(answers=[["ERROR POST /v1/pod/orders/init 500"]])
    source.truncated = True
    source.oldest = REPORTED_AT - timedelta(minutes=2)
    source.newest = REPORTED_AT
    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    (said,) = [line for line in result["not_checked"] if "capped" in line]
    # The span itself, which is the whole of the fix — not merely the word
    # "capped", which the sentence it replaced also carried.
    assert "2026-09-20T04:38:46Z–2026-09-20T04:40:46Z" in said
    # The window asked for is `since`→`reported_at + MARGIN`: 35 minutes, not
    # the 30 of FIRST_WINDOW. A sentence corrected for accuracy that is five
    # minutes out is a sentence that still misleads.
    assert "of the 35m asked for" in said
    assert "this request's own lines came from a separate read" in said


async def test_the_request_is_found_when_the_window_read_misses_it_entirely(db):
    """**The production shape, measured 2026-09-21.** A thirty-five minute
    window of `backend-reelme-v2` is ~12,400 lines; `limit=400` returned the
    newest eighty-four seconds of it, and the 400 under investigation had
    happened twenty-three minutes earlier. Filtering after the read cannot
    recover a line the read never fetched, so the read itself is narrowed.

    The fake stands in for that exactly: the window read answers with lines
    that do not contain the request, and only the narrowed read reaches it.
    """
    await write_rows(db)

    class Tail:
        """A back end whose cap is a tail, and whose filter is not."""

        name = "kubectl"
        decisive = 'ERROR /v1/pod/orders/init ERR19 "categoryId must be a UUID"'

        async def lines(self, placement, *, since, until, limit, needle=""):
            from friday.sdk.sources import Lines

            if needle:
                return Lines((self.decisive,), truncated=False)
            return Lines(
                tuple(f"INFO later {i}" for i in range(limit)), truncated=True
            )

    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": Tail()}})
    )

    assert status_of(result) == "ok"
    assert Tail.decisive in result["dossier"]


async def test_a_line_both_reads_returned_is_not_counted_or_quoted_twice(db):
    """The two reads overlap whenever the window was not truncated — which
    is the ordinary case on a quiet service. The same line arriving twice
    would be quoted twice in the dossier and counted twice in the
    histogram, and a doubled `ERR19` is a number nobody can act on."""
    await write_rows(db)
    source = FakeSource(answers=[[
        '{"level":"ERROR","correlationId":"abc-123","errorCode":"ERR19"}'
    ]])
    state = prepared(correlation_id="abc-123").with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert result["kept"] == 1
    assert dict(result["histogram"])["ERR19"] == 1


async def test_the_path_is_a_fallback_and_not_an_addition(db):
    """D2: "a curl, **or** an endpoint plus one identifier" — *or*.

    Measured on the production case, 2026-09-21: matching the path as well
    as the correlationId made the dossier 18 lines instead of 8, and all ten
    extra lines were *other people's successful calls to the same endpoint*
    with two lines of context each. The request's own lines were the same
    two either way.
    """
    await write_rows(db)
    others = [
        f'{{"correlationId":"someone-{i}","path":"/v1/pod/orders/init",'
        f'"statusCode":200}}'
        for i in range(6)
    ]
    source = FakeSource(answers=[[
        *others,
        '{"level":"ERROR","correlationId":"abc-123",'
        '"path":"/v1/pod/orders/init","statusCode":500}',
    ]])
    state = prepared(correlation_id="abc-123").with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert "abc-123" in result["dossier"]
    assert "someone-0" not in result["dossier"]


async def test_a_correlation_id_the_log_never_carries_falls_back_to_the_path(db):
    """The reporter read it off a response body, or a gateway rewrote it.
    Dropping the path because an id was *supplied* — rather than because it
    matched — would leave a dossier of nothing."""
    await write_rows(db)
    # Deliberately **not** a loud line: a line carrying ERROR is kept by
    # `_LOUD` whether or not the path is a needle, so a fixture like that
    # passes this test with the fallback deleted. It did, once.
    source = FakeSource(answers=[[
        '{"level":"INFO","path":"/v1/pod/orders/init","statusCode":500}'
    ]])
    state = prepared(correlation_id="never-logged").with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert "/v1/pod/orders/init" in result["dossier"]


async def test_a_correlation_id_that_matched_nothing_is_said_out_loud(db):
    """The most actionable thing a dossier can carry: the id is wrong, or
    this is not the service that logged it, or the window is. Detecting it
    and then discarding the detection — which the first version did — turns
    a finding into a search that quietly widened itself."""
    await write_rows(db)
    source = FakeSource(answers=[[
        '{"level":"ERROR","path":"/v1/pod/orders/init","statusCode":500}'
    ]])
    state = prepared(correlation_id="never-logged").with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert any(
        "carries the correlationId 'never-logged'" in line
        for line in result["not_checked"]
    )


async def test_the_widened_window_narrows_the_same_way_the_first_one_did(db):
    """Its own test because it is its own call site: replacing the rule with
    the old one *in the widened branch alone* left the whole suite green."""
    await write_rows(db)
    quiet = [f'{{"level":"INFO","n":{i}}}' for i in range(3)]
    loud = [
        *[f'{{"correlationId":"someone-{i}","path":"/v1/pod/orders/init"}}'
          for i in range(6)],
        '{"level":"ERROR","correlationId":"abc-123","path":"/v1/pod/orders/init"}',
    ]
    source = FakeSource(answers=[quiet, loud])
    state = prepared(correlation_id="abc-123").with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert source.windows == ["0.58h", "6.08h"], "it widened"
    assert "abc-123" in result["dossier"]
    assert "someone-0" not in result["dossier"]


async def test_the_narrowed_read_asks_for_the_correlation_id_over_the_path(db):
    """Both are substrings a back end can search for, and the correlationId
    names one request where the path names every call of that endpoint."""
    await write_rows(db)
    source = FakeSource(answers=[["ERROR abc-123 /v1/pod/orders/init 500"]])
    state = prepared(correlation_id="abc-123").with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert [needle for *_, needle in source.asked] == ["", "abc-123"]


async def test_with_nothing_naming_the_request_there_is_nothing_to_narrow_to(db):
    """No correlationId and no curl: the window read is all there is, and
    asking the back end to search for the empty string would return it."""
    await write_rows(db)
    source = FakeSource(answers=[["ERROR something"]])
    state = prepared(curl=None).with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert [needle for *_, needle in source.asked] == [""]


async def test_a_window_of_routine_warnings_is_still_worth_widening(db):
    """**Measured on production, 2026-09-21.** `backend-reelme-v2` emits
    about seven WARN lines a minute of routine engine chatter — "Engine
    returned an unmappable node status … skipping node". With WARN counting
    as an error, `has_error` was true in every window this service will ever
    produce, so the spec's one automatic widening could never fire for it.
    A chatty service disarmed it permanently and silently."""
    await write_rows(db)
    chatter = [
        '{"level":"WARN","msg":"Engine returned an unmappable node status '
        f'for run {i}: \\"awaiting-callback\\" — skipping node"}}'
        for i in range(20)
    ]
    source = FakeSource(answers=[chatter, ["ERROR /v1/pod/orders/init 500"]])
    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert source.windows == ["0.58h", "6.08h"], "the widening fired"


async def test_a_request_found_only_by_the_narrowed_read_is_not_out_of_reach(db):
    """`out_of_reach` means "the log does not go back that far", and it is
    read off the *window* read — whose lines can all fall outside the window
    while the narrowed read holds the request. Without this the production
    shape reports a request it found as one it could not search for."""
    await write_rows(db)

    class Retained:
        name = "kubectl"

        async def lines(self, placement, *, since, until, limit, needle=""):
            from friday.sdk.sources import Lines

            if needle:
                return Lines(("ERROR /v1/pod/orders/init 500",), oldest=since)
            # Nothing inside the window, and an oldest line newer than it —
            # which on its own reads as a pod that does not reach back.
            return Lines((), oldest=until)

    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": Retained()}})
    )

    assert status_of(result) == "ok"
    assert "does not reach back" not in " ".join(result["not_checked"])


async def test_the_seam_between_the_two_reads_is_marked(db):
    """They are put end to end but they are not contiguous — in the case
    this was built for the join is a twenty-three minute jump, and `distil`
    keeps a line either side of what it keeps. Unmarked, it reaches across
    and presents two unrelated spans as one story."""
    await write_rows(db)

    class Split:
        name = "kubectl"

        async def lines(self, placement, *, since, until, limit, needle=""):
            from friday.sdk.sources import Lines

            if needle:
                return Lines(("ERROR abc-123 /v1/pod/orders/init 500",))
            return Lines(tuple(f"INFO unrelated {i}" for i in range(3)))

    state = prepared(correlation_id="abc-123").with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": Split()}})
    )

    assert "a separate read narrowed to" in result["dossier"]


def _kubectl_returning(count: int):
    """A `SshKubectlSource` whose back end hands back `count` stamped lines.

    Through `lines()` rather than through `_within` directly: the thing
    under test is the *inference* that a read which came back exactly as
    long as its bound was cut short, and a test that hands `_within` the
    answer has already made that inference itself.
    """
    import asyncio

    from friday.sdk.sources import Placement
    from plugins.devops.sources.logs import SshKubectlSource

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
    from plugins.devops.sources.logs import _streams, _within

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

    from friday.sdk.sources import Placement, Reads
    from plugins.devops.sources.logs import LokiSource

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
    from plugins.devops.sources.logs import _logql

    assert _logql('a"b') == 'a\\"b'
    assert _logql("a\\b") == "a\\\\b"


def test_kubectl_searches_the_whole_window_rather_than_its_tail():
    """`--tail` is applied by the API server *before* anything downstream
    sees a line, so `--tail=400 | grep` searches the newest 400 lines and
    not the window. The whole window, narrowed on the far side by `grep`,
    searches all of it — and `head` keeps the answer bounded."""
    import asyncio

    from friday.sdk.sources import Placement
    from plugins.devops.sources.logs import SshKubectlSource

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

    from friday.sdk.sources import Placement
    from plugins.devops.sources.logs import SshKubectlSource

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

    from friday.sdk.sources import Placement
    from plugins.devops.sources.logs import SshKubectlSource

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


async def test_a_capped_narrowed_read_says_the_request_has_more_lines(db):
    """The other cap, and a different sentence: when even the search for
    this request hit its bound, the dossier is a prefix of one request
    rather than a sample of a window."""
    await write_rows(db)

    class Busy:
        name = "kubectl"

        async def lines(self, placement, *, since, until, limit, needle=""):
            from friday.sdk.sources import Lines

            if needle:
                return Lines(("ERROR abc-123 boom",), truncated=True)
            return Lines(("INFO something",))

    state = prepared(correlation_id="abc-123").with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": Busy()}})
    )

    assert any(
        "has more lines than were read" in line
        for line in result["not_checked"]
    )


async def test_a_reader_may_not_call_a_tool_it_did_not_declare():
    """The operator's call, 2026-09-21: which tools may be called is code,
    not configuration. The server this narrows also offers `release_apply`,
    `release_rollback` and `godaddy_dns_edit_record` — and a guard a file can
    widen is one the file's next editor widens by accident."""
    from friday.sdk.sources import Reads
    from plugins.devops.sources.logs import LokiSource

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
    from plugins.devops.sources import declared
    from plugins.devops.sources.db import DbSource
    from plugins.devops.sources.logs import LokiSource
    from plugins.devops.sources.release import ReleaseSource

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
    from plugins.devops.sources.code import original

    _built(tmp_path, js_line=3, ts_line=11)

    found = original(tmp_path / "dist" / "x.js", 3, tmp_path)

    assert found == ((tmp_path / "src" / "x.ts").resolve(), 11)


def test_a_compiled_file_with_no_map_beside_it_is_not_translated(tmp_path):
    """`None` means "read the built file and say so", never "guess"."""
    from plugins.devops.sources.code import original

    _built(tmp_path)
    (tmp_path / "dist" / "x.js.map").unlink()

    assert original(tmp_path / "dist" / "x.js", 3, tmp_path) is None


def test_a_map_that_does_not_parse_is_not_translated(tmp_path):
    from plugins.devops.sources.code import original

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

    from plugins.devops.sources.code import original

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

    from plugins.devops.sources.code import original

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

    from plugins.devops.sources.code import original

    _built(tmp_path, js_line=3, ts_line=11)
    map_file = tmp_path / "dist" / "x.js.map"
    loaded = json.loads(map_file.read_text())
    # line 1: a whole segment jumping +40. line 2: the same, corrupted.
    # line 3: +1 from wherever the carry left off.
    loaded["mappings"] = "AACA;AA!QA;AACA".replace("!", "!")
    map_file.write_text(json.dumps(loaded))

    assert original(tmp_path / "dist" / "x.js", 3, tmp_path) is None


async def test_the_counts_reach_the_model_and_the_report(db, tmp_path):
    """The histogram is evidence of a different kind: a code appearing forty
    times in the window is background, and the one appearing once beside this
    request is not. Forty lines say that forty times; one count says it
    once."""
    seen: list[str] = []

    class Reads:
        last_error = None

        async def run_structured(self, prompt, **kw):
            seen.append(prompt)
            return Diagnosis(
                cause="ERR306 là Midas nói qua ExceptionFilter",
                confidence="likely", conclusive=False, refs=["L1"],
            )

    state = (
        prepared()
        .with_result("find_request_log", {
            "status": "ok", "reason": "", "dossier": "ERROR ERR306 abc",
            "histogram": [["ERR951", 40], ["ERR306", 1]], "not_checked": [],
        })
        .with_result("read_failing_code", {"status": "empty", "reason": ""})
    )

    result = await diagnose_node(harness=Reads()).run(state, deps_for(db))
    await report_node(reports_dir=tmp_path).run(
        state.with_result("diagnose", result), deps_for(db)
    )

    assert "ERR951: 40" in seen[0] and "ERR306: 1" in seen[0]
    written = (tmp_path / "1.md").read_text()
    assert "`ERR951`: 40" in written


async def test_the_window_s_counts_survive_a_cut_that_drops_the_lines(db):
    """Counted before the cut. The point of counting is to say what the
    window held, which is not what survived being cut out of it."""
    await write_rows(db)
    # `"level":"error"` so these are loud lines and therefore candidates to
    # be quoted — `"errorCode"` alone is not, because `\berror\b` does not
    # match inside `errorCode`, and the histogram counts a wider set than the
    # cut ever considers quoting.
    source = FakeSource(answers=[[
        *[f'{{"level":"error","errorCode":"ERR951","n":{i}}}' for i in range(40)],
        '{"level":"error","errorCode":"ERR306","path":"/v1/pod/orders/init"}',
    ]])
    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert dict(result["histogram"])["ERR951"] == 40
    assert result["kept"] <= 12, "the spec's ceiling for this check"
    assert any("of 40" in line for line in result["not_checked"])


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
    from plugins.devops.sources.code import meanings

    doc = tmp_path / "docs" / "error-codes.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(CODES_DOC)

    found = meanings(doc, ("ERR19", "ERR306"), tmp_path)

    assert found == {
        "ERR19": "INTERNAL_ERROR — Something failed server-side",
        "ERR306": "BILLING_ERROR — Midas refused the charge",
    }


def test_a_code_the_doc_does_not_list_is_absent_rather_than_invented(tmp_path):
    from plugins.devops.sources.code import meanings

    doc = tmp_path / "error-codes.md"
    doc.write_text(CODES_DOC)

    assert meanings(doc, ("ERR999",), tmp_path) == {}


def test_a_doc_outside_the_clone_is_not_read(tmp_path):
    """`error_codes_doc` is a path from a row somebody typed, and this module
    checks a path before it opens it — the same rule as a stack frame."""
    from plugins.devops.sources.code import meanings

    outside = tmp_path / "outside" / "error-codes.md"
    outside.parent.mkdir(parents=True)
    outside.write_text(CODES_DOC)
    (tmp_path / "clone").mkdir()

    assert meanings(outside, ("ERR19",), tmp_path / "clone") == {}


async def test_the_node_carries_the_meanings_of_what_it_saw(db, tmp_path):
    doc = tmp_path / "error-codes.md"
    doc.write_text(CODES_DOC)
    state = (
        prepared()
        .with_result("resolve", {
            "status": "ok", "reason": "",
            "placement": {"env": "dev", "service": "s"},
            "project": {
                "name": "reelme", "repo_path": str(tmp_path),
                "error_codes_doc": str(doc),
            },
        })
        .with_result("find_request_log", {
            "status": "ok", "reason": "", "frames": [],
            "histogram": [["ERR19", 2104], ["ERR999", 1]],
        })
    )

    result = await read_failing_code_node().run(state, deps_for(db))

    assert result["codes"] == {"ERR19": "INTERNAL_ERROR — Something failed server-side"}
    assert "ERR999" not in result["codes"], "not in the doc, not invented"


def test_a_two_column_table_is_read_as_well_as_a_three(tmp_path):
    """The real document has both — 130 rows of `code | name | meaning` and
    69 of `code | meaning`. Wanting three silently dropped every Midas code,
    `ERR306` among them, which ticket 16 counted 3,455 times in 30 days."""
    from plugins.devops.sources.code import meanings

    doc = tmp_path / "error-codes.md"
    doc.write_text(
        "| Code | Meaning |\n"
        "| ---- | ------- |\n"
        "| `ERR306` | Content pack required |\n"
    )

    assert meanings(doc, ("ERR306",), tmp_path) == {
        "ERR306": "Content pack required"
    }


async def test_what_a_code_means_reaches_the_model(db):
    """`ERR19` is on every one of this service's HTTP 500s, so the number
    alone is not evidence of anything. What the repository says it means is
    the repository's word, not the model's recollection."""
    seen: list[str] = []

    class Reads:
        last_error = None

        async def run_structured(self, prompt, **kw):
            seen.append(prompt)
            return Diagnosis(
                cause="ERR19", confidence="possible", conclusive=False, refs=[],
            )

    state = (
        prepared()
        .with_result("find_request_log", {
            "status": "ok", "reason": "", "dossier": "ERROR ERR19",
            "histogram": [["ERR19", 2104]], "not_checked": [],
        })
        .with_result("read_failing_code", {
            "status": "empty", "reason": "no frame",
            "codes": {"ERR19": "INTERNAL_SERVER_ERROR — Internal server error"},
        })
    )

    await diagnose_node(harness=Reads()).run(state, deps_for(db))

    assert "ERR19: INTERNAL_SERVER_ERROR" in seen[0]
    assert "from the repository" in seen[0]


async def test_a_business_error_with_no_stack_is_still_worth_diagnosing(db):
    """A 4xx with a domain message carries no stack at all, and what its code
    means is the whole of what there is to read. Refusing to think without a
    dossier would refuse exactly the case the operator says is harder."""
    class Reads:
        last_error = None

        async def run_structured(self, prompt, **kw):
            return Diagnosis(
                cause="ERR306: content pack required", confidence="likely",
                conclusive=True, refs=[],
                # Filled so this reaches the refs gate rather than the
                # alternatives one — the two refuse for different reasons
                # and this test is about the first.
                alternatives_rejected=[{
                    "hypothesis": "the pack exists and the user lacks it",
                    "why": "the code means the pack itself is required",
                }],
            )

    state = (
        prepared()
        .with_result("find_request_log", {"status": "empty", "reason": "no line"})
        .with_result("read_failing_code", {
            "status": "empty", "reason": "no frame",
            "codes": {"ERR306": "Content pack required"},
        })
    )

    result = await diagnose_node(harness=Reads()).run(state, deps_for(db))

    assert status_of(result) == "empty", "conclusive with no ref is still refused"
    assert "pointed at nothing" in result["reason"]


# --- the clock (ticket 06) ---------------------------------------------------


def test_every_node_in_the_graph_has_a_ceiling():
    """**A node without one is not bounded by a big number, it is
    unbounded.** Three of these had none: `resolve`, `acknowledge` and
    `report` each reach the database, and a hung connection would hold a
    pool slot for the life of the process — the thing ticket 13 went to some
    trouble to prevent."""
    dag = _dag()

    assert [n.name for n in dag.nodes if n.timeout_seconds is None] == []


def test_a_graph_that_can_outlast_its_budget_is_refused_at_boot():
    """A clock that does not add up is a configuration mistake, and the
    place to find one is the start — not a cancelled run on a task nobody is
    watching."""
    import pytest as _pytest

    from friday.kernel.config import ConfigError
    from friday.kernel.dag.router import check_graph_clocks

    dag = _dag()
    total = sum(n.timeout_seconds or 0.0 for n in dag.nodes)

    check_graph_clocks([dag], {dag.name: total})
    with _pytest.raises(ConfigError, match="over the"):
        check_graph_clocks([dag], {dag.name: total - 1})


def test_a_node_with_no_clock_is_refused_however_large_the_budget():
    """The sum of a list with a hole in it is not a bound."""
    import pytest as _pytest

    from friday.kernel.config import ConfigError
    from friday.sdk.workflow import DAG, Node

    async def _nothing(state, deps):
        return None

    loose = DAG(
        name="devops.api_issue",
        nodes=(Node("a", _nothing, timeout_seconds=1.0), Node("b", _nothing)),
        edges=(),
    )

    with _pytest.raises(ConfigError, match="no timeout"):
        check_graph_clocks_for(loose)


def check_graph_clocks_for(dag):
    from friday.kernel.dag.router import check_graph_clocks

    return check_graph_clocks([dag], {dag.name: 10_000.0})


def test_a_graph_with_no_budget_is_not_checked():
    """The honest default for a one-node graph: its single node's clock is
    the bound, and inventing a budget for it would be inventing a number."""
    from friday.sdk.workflow import DAG, Node

    async def _nothing(state, deps):
        return None

    from friday.kernel.dag.router import check_graph_clocks

    check_graph_clocks(
        [DAG(name="doc_question", nodes=(Node("a", _nothing),), edges=())], {}
    )


def test_the_budget_is_read_as_a_number_and_must_be_positive(tmp_path):
    """Every other setting in this block is a name or a path and is read as
    text; `str()` on this one would compare a string to a sum."""
    import pytest as _pytest

    from plugins.devops.config import ConfigError, load_devops_config

    assert load_devops_config({"timeout_seconds": 300}).timeout_seconds == 300.0

    with _pytest.raises(ConfigError, match="must be a number"):
        load_devops_config({"timeout_seconds": "soon"})

    with _pytest.raises(ConfigError, match="must be positive"):
        load_devops_config({"timeout_seconds": 0})


def test_the_boot_actually_checks_the_graph_clocks(monkeypatch):
    """`check_graphs` is what the composition root calls before it opens
    anything. A check that exists and is never reached is a check nobody
    has — and removing the call from it left the whole suite green."""
    import pytest as _pytest

    from friday.kernel.config import ConfigError
    from friday.kernel.dag import router

    def over_budget(*_a, **_k):
        raise ConfigError("clocks do not add up")

    monkeypatch.setattr(router, "check_graph_clocks", over_budget)

    from dotenv import load_dotenv

    load_dotenv()
    with _pytest.raises(ConfigError, match="do not add up"):
        router.check_graphs(load_config_for_test())


def load_config_for_test():
    from friday.kernel.config import load_config

    return load_config()


async def test_the_endpoint_is_not_matched_beside_a_narrower_id(db):
    """`distil` ORs what it is given, so handing it the endpoint *as well as*
    the identifier is handing it the endpoint. Measured on this shape: eight
    other callers of the same path plus this reporter distils to 9 lines with
    both and 3 with the identifier alone.

    The node got the ordering right for the *source* narrowing and not for
    the distillation — one lesson applied in one of the two places it
    governs."""
    await write_rows(db)
    source = FakeSource(answers=[[
        *[f'{{"correlationId":"someone-{i}","path":"/v1/pod/orders/init",'
          f'"statusCode":200}}' for i in range(8)],
        '{"level":"ERROR","deviceId":"DEV-42","path":"/v1/pod/orders/init"}',
    ]])
    state = prepared(
        curl=None, endpoint="/v1/pod/orders/init", identifier="DEV-42",
    ).with_result("resolve", await resolve_node().run(prepared(), deps_for(db)))

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert "DEV-42" in result["dossier"]
    assert "someone-0" not in result["dossier"]


async def test_a_dead_correlation_id_still_says_so_when_the_endpoint_matched(db):
    """The endpoint happening to match is not a reason to stop reporting that
    the id did not — folding the note into the choice dropped this sentence
    the first time it was written."""
    await write_rows(db)
    source = FakeSource(answers=[[
        '{"level":"ERROR","path":"/v1/pod/orders/init","statusCode":500}'
    ]])
    state = prepared(correlation_id="never-logged").with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert "/v1/pod/orders/init" in result["dossier"], "the fallback still ran"
    assert any("never-logged" in line for line in result["not_checked"])


# --- reading the code that is actually running (ticket 04) -------------------


def test_the_running_tag_is_read_out_of_the_release_answer():
    """Measured 2026-09-21: `release_status` answers 104,761 characters, of
    which the tag is one field. It is read out at the source so nothing above
    ever holds a rendered Helm chart — in memory or in a prompt."""
    import asyncio
    import json

    from plugins.devops.sources.release import ReleaseSource

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

    from plugins.devops.sources.release import ReleaseSource

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

    from plugins.devops.sources.code import at_ref

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
    from plugins.devops.sources import code as code_source

    ran = []
    monkeypatch.setattr(
        code_source.subprocess, "run", lambda *a, **k: ran.append(a) or (_ for _ in ()).throw(AssertionError("git was run"))
    )

    assert code_source.at_ref(str(tmp_path), tmp_path / "a.ts", "--upload-pack=x") is None
    assert code_source.at_ref(str(tmp_path), tmp_path / "a.ts", "") is None
    assert ran == []


def _clone_with_tag(tmp_path, *, released: str, now: str):
    """A clone whose tag `1.0.0` holds `released` and whose tree holds `now`."""
    import subprocess

    root = tmp_path / "clone"
    root.mkdir()
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(root), *a], capture_output=True, text=True, check=True
    )
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (root / "orders.ts").write_text(released)
    run("add", "orders.ts")
    run("commit", "-qm", "released")
    run("tag", "1.0.0")
    (root / "orders.ts").write_text(now)
    return root


class Release:
    def __init__(self, tag="1.0.0"):
        self.tag = tag
        self.asked = []

    async def running_tag(self, project, env):
        self.asked.append((project, env))
        return self.tag


async def _code_for(db, root, release=None, env="production"):
    await write_rows(db, env=env, repo=str(root))
    curl = PROD_CURL if env == "production" else CURL
    resolve = await resolve_node().run(prepared(curl=curl), deps_for(db))
    state = (
        prepared(curl=curl)
        .with_result("resolve", resolve)
        .with_result(
            "find_request_log",
            {"status": "ok", "reason": "", "frames": [["/app/orders.ts", 3]]},
        )
    )
    extra = {} if release is None else {"release_source": release}
    return await read_failing_code_node().run(state, deps_for(db, extra=extra))


async def test_code_identical_to_the_running_tag_says_so_instead_of_hedging(
    db, tmp_path
):
    """The question is not "which ref shall I read" but "is what I just read
    the code that is running". When the clone's copy is byte-identical to the
    tag's, HEAD *is* the running version for this file, and saying so is
    worth more than the standing caveat."""
    same = "\n".join(f"line {i}" for i in range(20))
    root = _clone_with_tag(tmp_path, released=same, now=same)

    result = await _code_for(db, root, Release("1.0.0"))

    assert "identical to the running tag 1.0.0" in result["code"]
    assert not any("HEAD" in line for line in result["not_checked"])


async def test_code_that_differs_shows_the_tag_s_copy_and_says_which(db, tmp_path):
    """What the operator is told about is what production is running. The
    clone is whatever the last person checked out."""
    root = _clone_with_tag(
        tmp_path,
        released="\n".join(f"released {i}" for i in range(20)),
        now="\n".join(f"working tree {i}" for i in range(20)),
    )

    result = await _code_for(db, root, Release("1.0.0"))

    assert "released 2" in result["code"]
    assert "working tree 2" not in result["code"]
    assert any("differs from the running tag 1.0.0" in l for l in result["not_checked"])


async def test_with_the_version_unresolved_it_still_reads_and_still_says_so(
    db, tmp_path
):
    """Not knowing which version runs is not a failed investigation — but it
    is not silence either."""
    root = _clone_with_tag(tmp_path, released="a\nb\nc\nd\n", now="a\nb\nc\nd\n")

    result = await _code_for(db, root, Release(""))

    assert status_of(result) == "ok"
    assert any(
        "could not be resolved" in line for line in result["not_checked"]
    )


async def test_dev_is_not_asked_which_tag_is_running(db, tmp_path):
    """Dev deploys from a branch. There is no tag to compare against, and
    asking would be a round trip for an answer that does not exist."""
    root = _clone_with_tag(tmp_path, released="a\nb\nc\nd\n", now="a\nb\nc\nd\n")
    release = Release("1.0.0")

    await _code_for(db, root, release, env="dev")

    assert release.asked == []


# --- searched and not found, so the reporter is asked (ticket 02) ------------


async def test_a_searched_window_that_holds_nothing_asks_the_reporter(db):
    """The log reaches back and the request is not in it, so the likeliest
    reasons are answerable: the id was read off something else, the endpoint
    was named loosely, or it happened outside the window measured from their
    message. D2 asks for the response — where a correlationId actually comes
    from — and for when."""
    await write_rows(db)
    source = FakeSource(answers=[["GET /v1/health 200"], ["GET /v1/health 200"]])
    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert isinstance(result, Ask)
    assert "response" in result.text


async def test_a_reporter_who_gave_everything_is_not_asked_again(db):
    """Asking someone who already answered is how a system teaches people to
    stop answering it. They get the honest "searched and not found"."""
    await write_rows(db)
    source = FakeSource(answers=[["GET /v1/health 200"], ["GET /v1/health 200"]])
    said = prepared(response="artifact-1", correlation_id="abc-123")
    state = said.with_result("resolve", await resolve_node().run(said, deps_for(db)))

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert not isinstance(result, Ask)
    assert status_of(result) == "empty"


async def test_a_log_that_does_not_reach_back_is_not_a_question(db):
    """**The one case that must not become a question.** Every line the
    window could have held is gone; asking a reporter to re-send something
    into a log that no longer reaches back is asking for nothing."""
    await write_rows(db)
    source = FakeSource(answers=[[]], oldest=REPORTED_AT + timedelta(hours=16))
    state = prepared().with_result(
        "resolve", await resolve_node().run(prepared(), deps_for(db))
    )

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert not isinstance(result, Ask)
    assert "does not reach back" in " ".join(result["not_checked"])


def test_the_asking_node_does_not_end_the_graph_by_its_edges():
    """An `Ask` ends the pass in the runner, not by an edge — the edge out of
    this node is unconditional, so the resumed run walks on when the node
    answers with a dossier instead."""
    dag = _dag()
    ok = DAGState.empty().with_result(
        "find_request_log", {"status": "ok", "reason": ""}
    )

    assert dag.next_after("find_request_log", ok) == "read_failing_code"


async def test_a_response_with_nothing_naming_the_request_asks_for_the_endpoint(db):
    """The other half of D2. They pasted a response but named no request —
    no curl, no endpoint, no id — so there is nothing to search the log
    for, and that is the question."""
    await write_rows(db)
    source = FakeSource(answers=[["GET /v1/health 200"], ["GET /v1/health 200"]])
    said = prepared(curl=None, response="artifact-1")
    state = said.with_result("resolve", await resolve_node().run(prepared(), deps_for(db)))

    result = await find_request_log_node().run(
        state, deps_for(db, extra={"log_sources": {"kubectl": source}})
    )

    assert isinstance(result, Ask)
    assert "endpoint" in result.text and "deviceId" in result.text


# --- the shape forces the question (ticket 05) ------------------------------


def _answering(**kw):
    """A harness that answers with one fixed `Diagnosis`."""
    class Said:
        last_error = None

        async def run_structured(self, prompt, **_):
            return Diagnosis(**kw)

    return Said()


def _with_dossier():
    return (
        prepared()
        .with_result(
            "find_request_log",
            {"status": "ok", "reason": "", "dossier": "ERROR boom"},
        )
        .with_result("read_failing_code", {"status": "empty", "reason": ""})
    )


async def test_conclusive_without_a_rejected_alternative_is_refused(db):
    """Spec, tier 1 of self-questioning: the answer shape forces it. A cause
    nothing was weighed against is the first thing the evidence suggested —
    which is exactly the answer a reader cannot tell from a considered one."""
    result = await diagnose_node(harness=_answering(
        cause="x", confidence="certain", conclusive=True, refs=["L1"],
    )).run(_with_dossier(), deps_for(db))

    assert status_of(result) == "empty"
    assert "ruled out" in result["reason"]


async def test_an_alternative_with_no_reason_does_not_satisfy_the_gate(db):
    """An empty hypothesis rules nothing out and a reason with no substance
    is an assertion. Counting the field's existence would let the gate be
    satisfied by the shape rather than by the thinking."""
    result = await diagnose_node(harness=_answering(
        cause="x", confidence="certain", conclusive=True, refs=["L1"],
        alternatives_rejected=[{"hypothesis": "   ", "why": ""}, "not a dict"],
    )).run(_with_dossier(), deps_for(db))

    assert status_of(result) == "empty"
    assert "ruled out" in result["reason"]


async def test_a_weighed_conclusive_answer_is_reported(db):
    result = await diagnose_node(harness=_answering(
        cause="x", confidence="certain", conclusive=True, refs=["L1"],
        alternatives_rejected=[
            {"hypothesis": "downstream timeout", "why": "no timeout line",
             "ref": "L1"}
        ],
    )).run(_with_dossier(), deps_for(db))

    assert status_of(result) == "ok"
    assert result["diagnosis"]["alternatives_rejected"][0]["ref"] == "L1"


async def test_an_alternative_pointing_at_a_line_it_was_not_shown_voids_it(db):
    """It is shown to the operator as evidence something was ruled out, so an
    id naming no line is the same invention the main refs are checked for. A
    gate that checked half the answer is a gate a model learns the shape of."""
    result = await diagnose_node(harness=_answering(
        cause="x", confidence="certain", conclusive=True, refs=["L1"],
        alternatives_rejected=[
            {"hypothesis": "y", "why": "z", "ref": "L99"}
        ],
    )).run(_with_dossier(), deps_for(db))

    assert status_of(result) == "empty"
    assert "L99" in result["reason"]


async def test_a_tentative_answer_needs_no_alternative(db):
    """`conclusive: false` costs nothing, and the gate is the claim of
    certainty — not a tax on every answer."""
    result = await diagnose_node(harness=_answering(
        cause="có thể do cache", confidence="likely", conclusive=False,
        refs=["L1"],
    )).run(_with_dossier(), deps_for(db))

    assert status_of(result) == "ok"


def test_the_model_is_told_both_to_fill_it_and_what_happens_if_it_does_not():
    """A rule enforced in code and absent from the prompt is a rule the model
    discovers by having its whole answer thrown away.

    Two separate things, asserted separately: the instruction to name an
    alternative, and the warning that claiming `conclusive` without one is
    refused. Asserting only the field name passed with the warning deleted,
    because the instruction mentions it too."""
    from plugins.devops.graph.prompt import build_instructions

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

    from plugins.devops.sources import code as code_source

    def explode(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(code_source.subprocess, "run", explode)
    assert code_source.at_ref(str(tmp_path), tmp_path / "a.ts", "1.0.0") is None

    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=20)

    monkeypatch.setattr(code_source.subprocess, "run", hang)
    assert code_source.at_ref(str(tmp_path), tmp_path / "a.ts", "1.0.0") is None


def test_the_reads_switch_is_true_or_false_and_nothing_else(tmp_path):
    """It decides whether a pipeline that works is replaced by one that
    costs several model calls. A string that happens to be truthy is not a
    decision anybody made."""
    import pytest as _pytest

    from plugins.devops.config import ConfigError, load_devops_config

    assert load_devops_config({"diagnose_reads": True}).diagnose_reads is True

    with _pytest.raises(ConfigError, match="true or false"):
        load_devops_config({"diagnose_reads": "yes"})


async def test_the_switch_decides_which_way_diagnose_answers(db):
    """Off by default, and the switch is what lets the eval run the same
    cases both ways — a graph that read for itself regardless would have
    nothing to compare against.

    Asserted by running the node and seeing whether the factory was reached.
    The first version of this test built both graphs and asserted `True`; it
    was written while fixing tests that guarded nothing.
    """
    reached = []

    class SpyCaps(_StubCaps):
        # The v3.3 factory is invoked with this run's `tools`; the boot-time
        # build gets none. Recording only the tool'd calls records the factory,
        # which the plugin's builder wires only when `diagnose_reads`.
        def make_harness(self, *, agent, instructions, answers=None, tools=None):
            if tools is not None:
                reached.append(tools)
            return None  # "no agent configured" — the node skips, which is enough

    state = (
        prepared()
        .with_result("resolve", {
            "status": "ok", "reason": "",
            "placement": {"env": "dev", "service": "s", "pod_pattern": "p"},
            "project": {"repo_path": "/nowhere"},
        })
        .with_result("find_request_log", {"status": "empty", "reason": "none"})
        .with_result("read_failing_code", {"status": "empty", "reason": "none"})
    )

    for reads in (False, True):
        reached.clear()
        # The gating (a factory only when `diagnose_reads`) lives in the plugin's
        # graph builder now — build the graph with the switch and run its
        # diagnose node.
        api = SimpleNamespace(
            caps=SpyCaps(budget_tokens=1000),
            config=DevopsConfig(diagnose_reads=reads),
        )
        dag = build_devops_dag(api)
        await dag.node("diagnose").run(state, deps_for(db))
        assert bool(reached) is reads, f"diagnose_reads={reads}"


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
