"""Run one `backend.trace_problem` case through the spine's diagnose step
again, and say what it read and what it concluded.

Ticket 00 says "five runs through `run_agent.py` against a throwaway
database". That is one run per reporter willing to send the message again,
and it cannot be repeated after a prompt changes. This replays a case against
a copy of the store — so it can be run now, run again after a change, and
scored by the `backend.trace_problem` eval (`run_eval.py`), which runs this
same path.

**Speed is the point, and it is not about convenience.** Measured
2026-09-21: a dev pod's log history is its last restart, and two probes an
hour apart saw two different oldest lines. A report answered a day later
cannot be investigated at all on dev, so the window in which a case is worth
running is short enough that a one-command run is the difference between
collecting a case and not.

**On the spine since build-the-spine ticket 14.** Core Intake builds the
context from the case's own words; `backend.diagnose` runs through
`run_agent` — its toolsets, its grounding check — with the brief the first
plan gives it (board `domains-plug-in` ticket 15). The Planner and the draft
are not run: this scores the diagnosis. Without `--diagnose` nothing calls a
model, and the run shows where Intake placed the case.

    uv run replay_case.py 6                 # Intake only, no model
    uv run replay_case.py 6 --diagnose      # the diagnose step
    uv run replay_case.py --case data/cases/x.json --diagnose   # offline, for ever
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shlex
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field, fields
from dataclasses import replace as _dc_replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from dotenv import load_dotenv

from friday.kernel.config import load_config
from friday.kernel.dag import adapter
from friday.kernel.harness.run_agent import run_agent
from friday.kernel.spine.brief import agent_input
from friday.kernel.spine.intake import from_turns
from friday.kernel.toolsets.repos import REPOS
from friday.sdk.actions import Ask, HandOver
from friday.sdk.evidence import Evidence
from friday.sdk.toolset import RunContext
from friday.sdk.workflow import DAG, NodeRun
from friday.sdk.workflow import Deps as DAGDeps
from friday.store.db import Database
from plugins.backend.actions import TRACE_PROBLEM
from plugins.backend.agents import DIAGNOSE
from plugins.backend.params import TraceProblemParams
from plugins.backend.placement import enrich
from plugins.backend.toolsets import LOGS
from plugins.backend.toolsets.logs import LokiSource, SshKubectlSource, log_tools

#: The brief the first plan gives `backend.diagnose` — a goal, not a method
#: (build-the-spine ticket 20): fixed here so a score moves with the agent,
#: not with the Planner.
BRIEF = (
    "Establish which field, rule or call rejected the reporter's request, "
    "and whose fault it is."
)


def _canned_toolsets(name: str, source: Any) -> tuple[Any, ...]:
    """`backend.logs` reading the captured case, beside the real
    `core.repos` — the same tool, with only the log source replaced."""
    return (
        _dc_replace(LOGS, factory=lambda run: log_tools(run, {name: source})),
        REPOS,
    )


async def _run_on_adapter(
    dag: DAG,
    *,
    deps: DAGDeps,
    seed: dict[str, Any],
    system_db: Path,
    wfid: str,
) -> tuple[dict[str, Any], list[NodeRun], float]:
    """Run one graph through the DBOS adapter on a throwaway SQLite system
    database, the way the pool runs it in production — replacing the
    hand-written runner this tool used before the DBOS port (ticket 06). The
    canned `deps` are handed back by a one-shot factory; `seed` pre-loads
    `prepare` so the walk starts at `resolve` (node 0 is not replayed).
    """
    runs: list[NodeRun] = []

    async def factory(_scope: dict[str, Any]) -> DAGDeps:
        return deps

    def recorder(_scope: dict[str, Any]):
        async def rec(run: NodeRun) -> None:
            runs.append(run)

        return rec

    adapter.launch("friday-replay", str(system_db))
    adapter.clear_graphs()
    adapter.register_graph(dag, factory, recorder_factory=recorder)
    try:
        started = time.monotonic()
        final = await adapter.run(dag.name, {"_seed": seed}, workflow_id=wfid)
        return final, runs, time.monotonic() - started
    finally:
        adapter.shutdown()
        adapter.clear_graphs()


def copy_aside(live: Path, into: Path) -> Path:
    """The live database, copied whole, including what is still in its
    write-ahead log.

    `cp` is the wrong tool and cost an hour to notice: SQLite in WAL mode
    keeps recent writes in `<name>-wal`, so a copied `.db` alone is missing
    exactly the rows somebody typed in a minute ago — which read as "nothing
    was entered" rather than as a stale copy.
    """
    copy = into / "replay.db"
    source = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    target = sqlite3.connect(copy)
    with target:
        source.backup(target)
    target.close()
    source.close()
    return copy


@dataclass(frozen=True, slots=True)
class Ran:
    """One diagnose step: where Intake placed the case, what the agent came
    to (a `Diagnosis`, or the `Ask`/`HandOver` it ended with; `None` without
    a model), what it was shown, and how long it took."""

    placement: Any
    outcome: Any
    evidence: Evidence
    wall_s: float

    @property
    def diagnosis(self) -> dict[str, Any] | None:
        """The diagnosis as data, the eval's input — `None` when the step
        asked, handed over, was voided by its check, or did not run."""
        if self.outcome is None or isinstance(self.outcome, (Ask, HandOver)):
            return None
        return asdict(self.outcome)


async def diagnose(
    db: Any,
    *,
    task_id: int,
    turns: tuple[str, ...],
    channel_id: str,
    reported_at: str,
    toolsets: tuple[Any, ...],
    config: Any,
    with_model: bool,
) -> Ran:
    """Intake over `turns`, then — `with_model` — the diagnose step."""
    started = time.monotonic()
    context = await from_turns(
        db,
        turns=turns,
        channel_id=channel_id,
        reported_at=reported_at,
        enricher=enrich,
    )
    evidence = Evidence()
    outcome = None
    if with_model:
        outcome = await run_agent(
            DIAGNOSE,
            config.tiers[DIAGNOSE.tier],
            TRACE_PROBLEM.contract,
            toolsets,
            RunContext(
                task_id=task_id,
                domain=context.domain,
                evidence=evidence,
                mcp={},
                reported_at=datetime.fromisoformat(reported_at),
            ),
            agent_input(BRIEF, context, {}),
        )
    return Ran(context.domain, outcome, evidence, time.monotonic() - started)


def render(name: int | str, ran: Ran, *, decisive: str | None = None) -> str:
    placement = ran.placement
    lines = [
        f"=== {name}: {ran.wall_s:.1f}s, "
        f"{'the diagnose step ran' if ran.outcome is not None else 'no model call'} ===",
        f"  placed: env={placement.env} service={placement.service or '—'} "
        f"repo={placement.repo_path or '—'}",
        f"  read: {ran.evidence.reads} reads, {len(ran.evidence.index)} lines shown",
        "",
    ]
    outcome = ran.outcome
    if isinstance(outcome, HandOver):
        lines.append(f"2. handed over: {outcome.reason}")
    elif isinstance(outcome, Ask):
        lines.append(f"2. asked the reporter: {outcome.text}")
    elif ran.diagnosis is not None:
        lines.append(f"2. cause: {ran.diagnosis.get('cause', '')}")
        lines.append(
            f"   {ran.diagnosis.get('confidence')} · "
            f"{'conclusive' if ran.diagnosis.get('conclusive') else 'not conclusive'}"
            f" · refs {', '.join(ran.diagnosis.get('refs') or ()) or 'none'}"
        )
    else:
        lines.append("2. no diagnosis this run")
    for line in ran.evidence.not_checked:
        lines.append(f"   not checked: {line}")
    if decisive:
        # A captured case wrote down the decisive line when it was captured:
        # question 1 is whether the run was ever shown it.
        shown = any(decisive in line for line in ran.evidence.index.values())
        lines += ["", f"1. the line you called decisive was read: {shown}"]
    return "\n".join(lines)


# A superset capture's line shape: "<rfc3339> <log line>", the same shape both
# back ends hand `_streams` / `_within`. `reads["superset"]` is the whole
# incident window, unfiltered — so an agentic loop that chooses its own needle
# and window is served any slice of it, the way the live back end would answer,
# instead of the single pre-narrowed pair a fixed pipeline left behind.
_SUPERSET_LINE = re.compile(r"^(?P<at>\d{4}-\d\d-\d\dT[\d:.]+Z)\s+(?P<line>.*)$")


def _needle_of_logql(query: str) -> str:
    """The string inside a LogQL `|= "..."` line filter, unescaped — the
    reverse of `logs._logql`. Empty when the query carries no filter."""
    found = re.search(r'\|=\s*"((?:\\.|[^"\\])*)"', str(query))
    if not found:
        return ""
    return found.group(1).replace('\\"', '"').replace("\\\\", "\\")


def _parse_rfc3339(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _superset_within(
    lines: Any,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    needle: str = "",
    limit: int = 0,
) -> tuple[list[str], bool]:
    """The lines a back end would return for one read of the superset: inside
    `[since, until]` when given, carrying `needle` when given, capped at
    `limit`. Returns `(kept, truncated)` — `truncated` is whether the cap
    dropped any, which is the flag Loki sets and `kubectl` cannot.

    The needle is matched against the *log line*, not its stamp — that is what
    Loki's `|=` sees. The window is matched against the stamp; a line with no
    stamp is kept (a `kubectl` warning ahead of the log), the same forgiveness
    `_within` shows.
    """
    kept: list[str] = []
    for raw in lines or ():
        found = _SUPERSET_LINE.match(str(raw))
        text = found.group("line") if found else str(raw)
        if needle and needle not in text:
            continue
        if found and (since or until):
            at = datetime.fromisoformat(found.group("at"))
            if since and at < since:
                continue
            if until and at > until:
                continue
        kept.append(str(raw))
    truncated = bool(limit) and len(kept) > limit
    return (kept[:limit] if limit else kept), truncated


def _kubectl_needle(remote: str) -> str:
    """The needle out of a canned `kubectl` read's `grep -F -- <needle>`.

    The remote is shell-quoted **twice**: `SshKubectlSource.lines` wraps the
    whole pipeline in `bash -o pipefail -c <shlex.quote(piped)>`, and `piped`
    already carries `shlex.quote(needle)`. Peel the `bash -c` layer first — so a
    needle with a space or an apostrophe is neither read still-quoted (an empty
    result) nor split mid-token (a crash) — then take the grep argument,
    anchored on ` || true` so a needle containing `||` is not cut short."""
    inner = (shlex.split(remote) or [remote])[-1]
    found = re.search(r"grep -F -- (.+?) \|\| true", inner)
    return shlex.split(found.group(1))[0] if found else ""


def _kubectl_limit(remote: str) -> int:
    """The cap out of a canned `kubectl` read — `head -n N` on the needle
    path, `--tail=N` on the plain one."""
    found = re.search(r"head -n (\d+)", remote) or re.search(r"--tail=(\d+)", remote)
    return int(found.group(1)) if found else 0


@dataclass(frozen=True, slots=True)
class CannedReads:
    """A `Reads` that answers out of a captured case instead of the network.

    **Why a captured case exists at all.** This ticket's five runs have not
    happened for a reason that is not going away: dev keeps only what a pod
    has logged since its last restart, so a case reported a day late cannot
    be investigated at all, and production needs a Keycloak session Friday
    does not have yet. A case captured once — its parameters *and* the log
    its back end returned — is replayable for ever, by anyone, offline.

    It is also the only way to ask the question this ticket is really for:
    whether a *change* to the distillation rule makes a past case better or
    worse. That needs the same lines twice, which a live back end cannot
    promise an hour apart.

    Deliberately narrow: it answers `call`, which is what `friday.sources.
    Reads` answers, so the real `LokiSource` runs against it — the parsing,
    the stamps, the stream merge and the `truncated` flag are all the real
    ones. Only the socket is missing.
    """

    #: Either `{"superset": [<"iso line">, …]}` — the whole captured window,
    #: filtered here the way Loki would — or the legacy `{"window": <answer>,
    #: "narrowed": <answer>}` pair, each exactly what the Loki tool returned on
    #: the day. A case with a superset serves any needle the loop chooses; a
    #: legacy case serves only the one it was captured for.
    reads: dict[str, Any]

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        superset = self.reads.get("superset")
        if superset is None:
            # Legacy capture: one window and one narrowed answer, chosen off
            # the `|=` the source built. A graph that widens sees the same
            # lines again — true to what that older capture holds, and no more.
            narrowed = "|=" in str(arguments.get("query", ""))
            return json.dumps(self.reads["narrowed" if narrowed else "window"])
        # Superset capture: narrow the whole window the way Loki does
        # server-side, so any needle/window the loop chooses is answered
        # faithfully rather than with the one pair a fixed pipeline left.
        kept, truncated = _superset_within(
            superset,
            since=_parse_rfc3339(arguments.get("start")),
            until=_parse_rfc3339(arguments.get("end")),
            needle=_needle_of_logql(arguments.get("query", "")),
            limit=int(arguments.get("limit", 0) or 0),
        )
        return json.dumps(
            {"streams": [{"labels": {}, "lines": kept}], "truncated": truncated}
        )


#: Everything a captured case may say that is not a parameter of the issue
#: itself. Named here so anything else is a mistake rather than a silence.
CASE_KEYS = frozenset(
    {
        "id",
        "channel_id",
        "reported_at",
        "reads",
        "source",
        # What the operator said was true, used by the `backend.trace_problem` eval
        # and by nothing at run time. `cause_mentions` is the tokens any correct
        # answer must contain; `conclusive` is whether the evidence really did
        # settle it, which is a judgement about their system and not about the
        # model.
        "decisive",
        "cause",
        "cause_mentions",
        "conclusive",
        "captured",
        "notes",
    }
)


def case_params(case: dict[str, Any]) -> dict[str, Any]:
    """The issue's parameters out of a captured case, refusing anything the
    file says that nothing will read.

    **A typo here is invisible and undoes the run.** Renaming
    `correlation_id` to `correlationId` in the file made the dossier 18
    lines instead of 8 — the request was matched by its endpoint path
    instead, pulling in every other user's successful call — and the tool
    reported it as a clean run. A hand-written file is the only input this
    has; an unknown key has to be an error.

    `dataclasses.fields` rather than `__annotations__`, which is only the
    names declared on the class itself and would start quietly dropping
    inherited parameters the day one of these grows a base class.
    """
    known = {f.name for f in fields(TraceProblemParams)}
    unknown = sorted(set(case) - known - CASE_KEYS)
    if unknown:
        raise ValueError(
            f"{unknown} is not a parameter of trace_problem nor part of a case. "
            f"Parameters: {sorted(known)}. Case: {sorted(CASE_KEYS)}."
        )
    for needed in ("id", "channel_id", "reported_at", "reads"):
        if needed not in case:
            raise ValueError(f"a captured case needs {needed!r}")
    return {k: v for k, v in case.items() if k in known}


def canned_source(case: dict[str, Any]) -> tuple[str, Any]:
    """The back end this case was captured from, answering from the file.

    Both real source classes, with only their transport replaced — so the
    parsing, the stamps, the clipping and the `truncated` flag are the ones
    that run in production. A capture that stored parsed lines would test
    this module's own parsing and pass while the source's was broken.

    **`kubectl` is here because dev is the reason captured cases exist.**
    Dev keeps only what a pod has logged since its last restart, which is
    what makes a dev case unrepeatable an hour later; a capture mechanism
    that served production only would cover the one environment that did
    not need it.
    """
    which = case.get("source", "loki")
    if which == "loki":
        return "loki", LokiSource(server=CannedReads(case["reads"]))
    if which == "kubectl":
        return "kubectl", CannedKubectl(reads=case["reads"])
    raise ValueError(f"a case is captured from loki or kubectl, not {which!r}")


@dataclass(frozen=True, slots=True)
class CannedKubectl(SshKubectlSource):
    """`SshKubectlSource` with the SSH taken out and the file put in."""

    reads: dict[str, Any] = field(default_factory=dict)

    async def _run(self, remote: str) -> str:
        if "get pods" in remote:
            return "pod/captured-0\n"
        superset = self.reads.get("superset")
        if superset is None:
            # The narrowed read is the one that greps; see the source itself.
            return str(self.reads["narrowed" if "grep -F" in remote else "window"])
        # Superset: `grep` the needle and `head` the cap the way the real
        # remote does; the window is re-applied by `_within` in `lines()`, so
        # it is not applied here.
        kept, _ = _superset_within(
            superset,
            needle=_kubectl_needle(remote),
            limit=_kubectl_limit(remote),
        )
        return "\n".join(kept) + ("\n" if kept else "")


def case_turns(case: dict[str, Any]) -> tuple[str, ...]:
    """What the reporter said, rebuilt from a captured case's parameters —
    the turns core Intake reads."""
    params = case_params(case)
    said = [params.get("summary") or "", params.get("curl") or ""]
    if params.get("correlation_id"):
        said.append(f"correlationId {params['correlation_id']}")
    if params.get("environment"):
        said.append(f"environment {params['environment']}")
    return tuple(t for t in said if t)


async def run_captured(case: dict[str, Any], *, with_model: bool, into: Path) -> Ran:
    """One captured case through the diagnose step. The eval runner and the
    command line share this one path: a second way to run a case is a second
    way for a score to disagree with what the operator sees."""
    config = load_config()
    into.mkdir(parents=True, exist_ok=True)
    db = await Database.connect(str(copy_aside(Path(config.database_path), into)))
    try:
        name, source = canned_source(case)
        return await diagnose(
            db,
            task_id=int(case["id"]) if str(case["id"]).isdigit() else 0,
            turns=case_turns(case),
            channel_id=str(case["channel_id"]),
            reported_at=case["reported_at"],
            toolsets=_canned_toolsets(name, source),
            config=config,
            with_model=with_model,
        )
    finally:
        await db.close()


async def replay_captured(case_path: Path, *, with_model: bool, into: Path) -> int:
    case = json.loads(case_path.read_text())
    ran = await run_captured(case, with_model=with_model, into=into)
    print(render(case["id"], ran, decisive=case.get("decisive")))
    if case.get("cause"):
        print(f"\n2. you said the cause is: {case['cause']}")
    return 0


async def replay(task_id: int, *, with_model: bool, into: Path) -> int:
    """A task already in the store, its own turns, the live read tools."""
    config = load_config()
    into.mkdir(parents=True, exist_ok=True)
    db = await Database.connect(str(copy_aside(Path(config.database_path), into)))
    try:
        task = await db.task(task_id)
        if task is None:
            print(f"no task {task_id}", file=sys.stderr)
            return 1
        if task.type != TRACE_PROBLEM.name:
            print(
                f"task {task_id} is {task.type}, not {TRACE_PROBLEM.name}",
                file=sys.stderr,
            )
            return 1
        ran = await diagnose(
            db,
            task_id=task.id,
            turns=tuple(await db.original_turns_for(task.id)),
            channel_id=task.conversation.channel_id,
            reported_at=(task.created_at or datetime.now(UTC)).isoformat(),
            toolsets=(LOGS, REPOS),
            config=config,
            with_model=with_model,
        )
        print(render(task_id, ran))
        return 0
    finally:
        await db.close()


def main() -> int:
    load_dotenv()
    # The trace_problem graph reads memory (environment/route/service rows) through
    # the store, which reads the kind registry (ticket 12); fill it before a run.
    from friday.kernel.memory.registry import register_all_memory_kinds

    register_all_memory_kinds()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "task_id",
        type=int,
        nargs="?",
        help="a task already in the store. Needs its back end reachable.",
    )
    parser.add_argument(
        "--case",
        type=Path,
        default=None,
        help="a captured case: its parameters and the log its back end "
        "returned, in one file. Replayable offline and for ever, which "
        "a dev pod's log is not.",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="run the diagnose step (a model call). Off by default: where "
        "Intake placed the case costs nothing to ask.",
    )
    parser.add_argument(
        "--into",
        type=Path,
        default=None,
        help="where the copy of the database goes. A temporary directory by "
        "default, so nothing here can touch the live database.",
    )
    args = parser.parse_args()
    if (args.task_id is None) == (args.case is None):
        parser.error("give a task id or --case, not both and not neither")
    into = args.into or Path(mkdtemp(prefix="friday-replay-"))
    if args.case is not None:
        return asyncio.run(
            replay_captured(args.case, with_model=args.diagnose, into=into)
        )
    return asyncio.run(replay(args.task_id, with_model=args.diagnose, into=into))


if __name__ == "__main__":
    raise SystemExit(main())
