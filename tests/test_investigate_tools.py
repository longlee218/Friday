"""Ticket 15 — the reads `Diagnose` drives itself.

The two things these guard are the two things that keep the change safe: the
grounding index has to **accumulate** across calls, or `refs` stop meaning
anything; and a model that can loop has to hit a ceiling, or the graph's
clock stops being arithmetic and becomes a hope.

Everything else here is about what a tool refuses to pretend — out of reach
is not not-found, a capped read says it was capped, and code read at the
clone says so rather than claiming to be what is deployed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from friday.sdk.sources import Lines, Placement
from plugins.devops.config import DEFAULT_CONTAINER_ROOTS
from plugins.devops.investigate import MAX_READS, Evidence, investigate_tools

AT = datetime(2026, 9, 21, 10, 40, tzinfo=timezone.utc)


class Log:
    """A log source answering from a script, recording what it was asked."""

    def __init__(self, *answers, oldest=None, truncated=False):
        self.answers = list(answers)
        self.oldest = oldest
        self.truncated = truncated
        self.asked: list[tuple] = []

    async def lines(self, placement, *, since, until, limit, needle=""):
        self.asked.append((since, until, needle))
        found = self.answers[min(len(self.asked) - 1, len(self.answers) - 1)]
        return Lines(tuple(found), self.oldest, truncated=self.truncated)


def built(source=None, project=None, tag=""):
    evidence = Evidence()
    tools = investigate_tools(
        evidence=evidence,
        placement=Placement(env="dev", service="s", pod_pattern="p"),
        project=project or {},
        log_sources={} if source is None else {"kubectl": source},
        release_tag=tag,
        reported_at=AT,
        container_roots=DEFAULT_CONTAINER_ROOTS,
    )
    # Plugin tools are neutral `ToolSpec`s; the harness binds each to the
    # vendor's `Tool`. Bind here so the test sees what the model sees (ticket 14).
    from friday.kernel.harness.harness import _bind_tool_spec

    return evidence, {t.name: t for t in (_bind_tool_spec(x) for x in tools)}


def call(tool, **kw):
    """Invoke a tool's own function with the model's arguments. These tools take
    no run context — they close over the evidence and placement a run resolved —
    so the call is the function directly, awaited if it is async."""
    import inspect

    result = tool.function(**kw)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


# --- the grounding index ----------------------------------------------------


def test_ids_continue_across_calls_so_a_late_line_is_as_citable_as_an_early_one():
    """With a fixed pipeline the index was built once from a dossier and a
    code excerpt. A model that reads four times needs `L7` to mean one line
    for the life of the run, whichever tool produced it."""
    evidence = Evidence()

    first = evidence.show(["a", "b"])
    second = evidence.show(["c"])

    assert first.startswith("L1 | a")
    assert second == "L3 | c"
    assert evidence.index == {"L1": "a", "L2": "b", "L3": "c"}


def test_every_line_shown_gets_an_id_of_its_own():
    """The whole of what the refs gate checks. Two different lines under one
    id is a citation that resolves to whichever was last.

    Asserted as "one id per line shown", not "no two lines share text" —
    which the first version asserted, and which is a property of the input,
    not of the numbering. It survived every mutation including `ref = "L1"`,
    and it *failed* on correct code the moment a window held the same line
    twice, which real logs do constantly."""
    evidence = Evidence()

    evidence.show(["first", "second"])
    evidence.show(["third"])

    assert len(evidence.index) == 3
    assert sorted(evidence.index) == ["L1", "L2", "L3"]


def test_the_same_line_seen_twice_keeps_one_id():
    """Windows overlap: a model that widens its search sees what it already
    saw. A second id for the same text pays for it twice and offers two ways
    to cite one thing."""
    evidence = Evidence()

    first = evidence.show(["ERROR boom", "quiet"])
    again = evidence.show(["ERROR boom"])

    assert first.startswith("L1 | ERROR boom")
    assert again == "L1 | ERROR boom"
    assert len(evidence.index) == 2


def test_a_blank_line_keeps_its_place_and_takes_no_id():
    """An id pointing at nothing is an id a model can cite to mean
    anything."""
    evidence = Evidence()

    shown = evidence.show(["a", "", "b"])

    assert shown == "L1 | a\n\nL2 | b"
    assert "" not in evidence.index.values()


# --- the ceiling ------------------------------------------------------------


def test_a_run_that_has_looked_enough_is_told_to_answer():
    """A fixed pipeline's clock was the sum of a known list of nodes. A model
    that can loop turns `api_issue.timeout_seconds` into a hope."""
    evidence = Evidence()
    evidence.reads = MAX_READS

    said = evidence.spent()

    assert str(MAX_READS) in said and "next_checks" in said


def test_the_ceiling_is_checked_by_the_tools_not_only_defined(tmp_path):
    source = Log(["ERROR abc"])
    evidence, tools = built(source)
    evidence.reads = MAX_READS

    said = call(tools["read_log"], needle="abc")

    assert "limit" in said
    assert source.asked == [], "and it did not read"


def test_every_read_counts_against_it():
    source = Log(["ERROR abc"], ["ERROR abc"])
    evidence, tools = built(source)

    call(tools["read_log"], needle="abc")
    call(tools["read_log"], needle="abc")

    assert evidence.reads == 2


# --- what a read refuses to pretend ----------------------------------------


def test_the_needle_narrows_the_read_rather_than_its_result():
    """The measured boundary: one raw window is ~43,654 tokens. The search
    runs where the log is."""
    source = Log(["ERROR abc-123"])
    _, tools = built(source)

    call(tools["read_log"], needle="abc-123")

    (_since, _until, needle) = source.asked[0]
    assert needle == "abc-123"


def test_the_model_chooses_the_window():
    source = Log(["ERROR abc"])
    _, tools = built(source)

    call(tools["read_log"], needle="abc", minutes_back=360)

    since, until, _ = source.asked[0]
    assert AT - since == timedelta(minutes=360)


def test_out_of_reach_is_not_not_found():
    """The difference between a request nobody can find and one nobody
    searched for."""
    source = Log([], oldest=AT + timedelta(hours=16))
    evidence, tools = built(source)

    said = call(tools["read_log"], needle="abc")

    assert "does not reach back" in said
    assert "not searched for" in said
    assert any("reach back" in line for line in evidence.not_checked)


def test_an_empty_window_that_does_reach_back_says_plainly_it_found_nothing():
    source = Log([], oldest=AT - timedelta(days=2))
    _, tools = built(source)

    said = call(tools["read_log"], needle="abc")

    assert "No line" in said
    assert "reach back" not in said


def test_a_capped_read_says_so_where_the_node_will_see_it():
    """The honest half has to survive the model choosing what to look at —
    it is not something a model can be relied on to say about itself."""
    source = Log(["ERROR abc"], truncated=True)
    evidence, tools = built(source)

    call(tools["read_log"], needle="abc")

    assert any("capped" in line for line in evidence.not_checked)


def test_a_source_that_is_down_is_an_answer_not_a_crash():
    class Broken:
        async def lines(self, *a, **kw):
            raise RuntimeError("no route to host")

    _, tools = built(Broken())

    said = call(tools["read_log"], needle="abc")

    assert "could not be read" in said and "no route to host" in said


def test_with_no_source_configured_it_says_which_one_it_wanted():
    _, tools = built(None)

    said = call(tools["read_log"], needle="abc")

    assert "kubectl" in said


# --- reading code -----------------------------------------------------------


def test_code_is_read_at_the_running_tag_and_says_which(tmp_path):
    import subprocess

    root = tmp_path / "clone"
    root.mkdir()
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(root), *a], capture_output=True, text=True, check=True
    )
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (root / "orders.ts").write_text("released\nb\nc\nd\n")
    run("add", "orders.ts")
    run("commit", "-qm", "one")
    run("tag", "1.0.0")
    (root / "orders.ts").write_text("edited since\nb\nc\nd\n")

    _, tools = built(project={"repo_path": str(root)}, tag="1.0.0")

    said = call(tools["read_code"], file="/app/orders.ts", line=1)

    assert "released" in said and "edited since" not in said
    assert "running tag 1.0.0" in said


def test_code_outside_the_clone_is_named_rather_than_opened():
    _, tools = built(project={"repo_path": "/nowhere"})

    said = call(tools["read_code"], file="/app/node_modules/x/y.js", line=3)

    assert "not in this clone" in said


def test_the_lines_of_code_are_citable_like_any_other(tmp_path):
    (tmp_path / "a.ts").write_text("one\ntwo\nthree\n")
    evidence, tools = built(project={"repo_path": str(tmp_path)})

    call(tools["read_code"], file="/app/a.ts", line=2)

    assert any("two" in line for line in evidence.index.values())


# --- what a code means ------------------------------------------------------


def test_a_project_with_no_table_says_so_rather_than_guessing():
    _, tools = built(project={})

    assert "records no error-code table" in call(
        tools["what_code_means"], code="ERR19"
    )


def test_the_window_reaches_past_the_report():
    """The old node's `MARGIN`, and for its reason: a log line is written
    before the person complains about it, but not always before their clock
    says so. Dropping it makes the five minutes around the report
    unreachable — which is where the line often is."""
    source = Log(["ERROR abc"])
    _, tools = built(source)

    call(tools["read_log"], needle="abc")

    _since, until, _ = source.asked[0]
    assert until > AT


def test_a_window_wider_than_anything_is_kept_is_brought_back_to_it():
    """Not distrust: thirty days is what Loki keeps, and a search that says
    it covered a year covered a month."""
    from plugins.devops.investigate import MAX_MINUTES_BACK

    source = Log(["ERROR abc"])
    _, tools = built(source)

    call(tools["read_log"], needle="abc", minutes_back=525_600)

    since, until, _ = source.asked[0]
    assert (until - since).days <= MAX_MINUTES_BACK // (60 * 24) + 1


def test_a_window_that_narrows_to_nothing_says_so_rather_than_nothing():
    """A back end is allowed to ignore `needle` — the protocol says so — and
    then nothing in the window is this request's and nothing is loud.
    Returning the empty string tells a model nothing at all."""
    source = Log(["INFO unrelated", "INFO also unrelated"])
    _, tools = built(source)

    said = call(tools["read_log"], needle="abc-123")

    assert said.strip()
    assert "none of them carries" in said


def test_the_error_codes_in_what_was_read_are_counted():
    """What `distil` actually contributes here, now that the narrowing does
    the cutting: the histogram. Without a test it could be removed from the
    tool with the suite green."""
    source = Log([
        '{"errorCode":"ERR19","correlationId":"abc"}',
        '{"errorCode":"ERR19","correlationId":"abc"}',
        '{"errorCode":"ERR306","correlationId":"abc"}',
    ])
    _, tools = built(source)

    said = call(tools["read_log"], needle="abc")

    assert "ERR19 ×2" in said and "ERR306 ×1" in said


def test_reading_code_counts_against_the_ceiling_too():
    """`spent()` says it is checked by every tool. One of them did not."""
    source = Log(["ERROR abc"])
    evidence, tools = built(source, project={"repo_path": "/nowhere"})
    evidence.reads = MAX_READS

    assert "limit" in call(tools["read_code"], file="/app/a.ts", line=1)


def test_asking_what_a_code_means_counts_too():
    evidence, tools = built(project={})
    evidence.reads = MAX_READS

    assert "limit" in call(tools["what_code_means"], code="ERR19")


def test_a_service_with_no_repository_recorded_says_so(tmp_path):
    _, tools = built(project={})

    assert "No repository is recorded" in call(
        tools["read_code"], file="/app/a.ts", line=1
    )


def test_a_code_the_table_does_not_carry_says_so(tmp_path):
    doc = tmp_path / "codes.md"
    doc.write_text("| Code | Meaning |\n| --- | --- |\n| ERR19 | Internal |\n")
    _, tools = built(project={
        "repo_path": str(tmp_path), "error_codes_doc": doc.name,
    })

    assert "not in this project's table" in call(
        tools["what_code_means"], code="ERR999"
    )


def test_a_clone_missing_the_running_tag_says_so_where_the_node_sees_it(tmp_path):
    """Degrading is correct; degrading silently is not."""
    (tmp_path / "a.ts").write_text("one\ntwo\n")
    evidence, tools = built(project={"repo_path": str(tmp_path)}, tag="9.9.9")

    call(tools["read_code"], file="/app/a.ts", line=1)

    assert any("9.9.9" in line for line in evidence.not_checked)


# --- Diagnose reading for itself (v3.3, ticket 15) --------------------------


def _state(db_rows_written=True):
    """A run that has resolved a placement and read nothing."""
    from friday.sdk.workflow import DAGState
    from plugins.devops.params import ApiIssueParams

    return (
        DAGState.empty()
        .with_result("prepare", ApiIssueParams(summary="500 khi init đơn"))
        .with_result("resolve", {
            "status": "ok", "reason": "",
            "placement": {
                "env": "dev", "service": "backend-reelme-v2",
                "pod_pattern": "backend-reelme-v2", "namespace": "dev",
            },
            "project": {"name": "reelme", "repo_path": "/nowhere"},
        })
    )


class Answering:
    """A harness that records the prompt and the tools it was built with."""

    last_error = None
    seen: dict = {}

    def __init__(self, answer, tools):
        self.answer = answer
        # Plugin tools arrive as neutral `ToolSpec`s; the real harness binds
        # each to the vendor's `Tool` in its constructor, so this stand-in does
        # too — then `.name` reads the same here as it does in production.
        from friday.kernel.harness.harness import _bind_tool_spec

        self._tools = [_bind_tool_spec(t) for t in tools or ()]
        Answering.seen = {"tools": [t.name for t in self._tools]}

    async def run_structured(self, prompt, **_):
        Answering.seen["prompt"] = prompt
        return self.answer


async def test_the_model_is_given_the_reads_and_told_where_things_live(db):
    """`Gather` gathers metadata under v3.3 — where the service runs, which
    clone holds its code — and nothing is read in advance."""
    from plugins.devops.graph.diagnose import Diagnosis, diagnose_node
    from plugins.devops.graph.deps import ApiIssueDeps
    from types import SimpleNamespace

    answer = Diagnosis(cause="x", confidence="likely", conclusive=False, refs=[])
    node = diagnose_node(make_harness=lambda *, tools: Answering(answer, tools))

    await node.run(_state(), ApiIssueDeps(
        task=SimpleNamespace(id=1, conversation=None, params={}, created_at=AT),
        db=db, sender="", approver="",
    ))

    assert set(Answering.seen["tools"]) == {"read_log", "read_code", "what_code_means"}
    # Each field named, not one string that three of them happen to contain:
    # asserting the pod pattern alone stayed green with `service:` deleted.
    said = Answering.seen["prompt"]
    assert "service: backend-reelme-v2" in said
    assert "environment: dev" in said
    assert "repository: /nowhere" in said
    assert "Nothing has been read for you" in said


async def test_an_answer_written_without_reading_anything_is_refused(db):
    """A cause from an endpoint's name alone reads exactly like one built
    from evidence, which is the whole reason the gates exist. Under the old
    pipeline the node checked there was a dossier; here nothing was fetched
    at all."""
    from plugins.devops.graph.diagnose import Diagnosis, diagnose_node
    from friday.sdk.workflow import status_of
    from plugins.devops.graph.deps import ApiIssueDeps
    from types import SimpleNamespace

    answer = Diagnosis(cause="chắc là do cache", confidence="likely",
                       conclusive=False, refs=[])
    node = diagnose_node(make_harness=lambda *, tools: Answering(answer, tools))

    result = await node.run(_state(), ApiIssueDeps(
        task=SimpleNamespace(id=1, conversation=None, params={}, created_at=AT),
        db=db, sender="", approver="",
    ))

    assert status_of(result) == "empty"
    assert "without reading a single line" in result["reason"]


async def test_answering_passes_through_one_copy_of_the_gates(db):
    """One path now (ticket 05: the fixed-dossier path is gone), and `_judged`
    is still its own function rather than inlined — the gates are the
    difference between a diagnosis and a plausible sentence, and that is
    worth keeping legible on its own even with one caller."""
    import inspect

    from plugins.devops.graph import diagnose as module

    source = inspect.getsource(module)

    assert source.count("without naming one") == 1, "the alternatives gate, once"
    assert source.count("which names no line it was shown") == 1, "the refs gate, once"
    assert source.count("_judged(") == 2, "defined once, called once"


def test_the_instructions_change_when_the_model_fetches_its_own_evidence():
    """A prompt saying "the lines you were shown" to a model that was shown
    nothing is a prompt it cannot obey. Asserted on the instructions, not on
    the run's input — deleting the reads half left every other test green."""
    from plugins.devops.graph.prompt import build_instructions

    plain, reading = build_instructions(), build_instructions(reads=True)

    assert "Nothing has been read for you" in reading
    assert "Nothing has been read for you" not in plain
    assert "read_log" in reading and "read_log" not in plain


async def test_what_a_tool_could_not_check_reaches_the_envelope(db):
    """The honest half is the tools' own, not the model's: what a read left
    out is a fact about the read, and a model asked to remember it
    reproduces it unreliably."""
    from plugins.devops.graph.diagnose import Diagnosis, diagnose_node
    from plugins.devops.graph.deps import ApiIssueDeps
    from types import SimpleNamespace

    answer = Diagnosis(cause="x", confidence="likely", conclusive=False,
                       refs=["L1"])

    class Reads(Answering):
        async def run_structured(self, prompt, **kw):
            # The model reads once; the source is out of reach, which the
            # tool records where the node will find it.
            await _tool_named(self, "read_log")(needle="abc")
            return await super().run_structured(prompt, **kw)

    source = Log([], oldest=AT + timedelta(hours=16))
    node = diagnose_node(make_harness=lambda *, tools: Reads(answer, tools))

    result = await node.run(_state(), ApiIssueDeps(
        task=SimpleNamespace(id=1, conversation=None, params={}, created_at=AT),
        db=db, sender="", approver="", log_sources={"kubectl": source},
    ))

    assert any("reach back" in line for line in result["not_checked"])


def _tool_named(harness, name):
    """The tool the harness was built with, callable as the model calls it —
    these tools take no run context, so the call is the function directly."""
    import inspect

    tool = next(t for t in harness._tools if t.name == name)

    async def invoke(**kw):
        result = tool.function(**kw)
        return await result if inspect.isawaitable(result) else result

    return invoke
