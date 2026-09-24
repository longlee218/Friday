"""Run one past task through the `api_issue` graph again, and answer ticket
00's four questions about it.

Ticket 00 says "five runs through `run_agent.py` against a throwaway
database". That is one run per reporter willing to send the message again,
and it cannot be repeated after a prompt changes. This replays a task that
is already in the store: same graph, same pool-facing nodes, same outbox
rows, against a copy — so a case can be run now, run again after a change,
and run a third time with the model on.

**Speed is the point, and it is not about convenience.** Measured
2026-09-21: a dev pod's log history is its last restart, and two probes an
hour apart saw two different oldest lines. A report answered a day later
cannot be investigated at all on dev, so the window in which a case is worth
running is short enough that a one-command run is the difference between
collecting a case and not.

**`Diagnose` is off unless asked for**, and that is the cheap loop. Question
1 — did the dossier hold the line the operator calls decisive — is a
question about `friday/kernel/dag/api_issue/distil.py`, a pure function. It can be
asked and re-asked for nothing. Only turn the model on once the dossier is
right, or pay for reasoning over a dossier nobody has checked.

    uv run replay_case.py 6                 # no model, ~5s
    uv run replay_case.py 6 --diagnose      # one model call
    uv run replay_case.py 6 --into ./out    # where the report goes
    uv run replay_case.py --case data/cases/x.json --diagnose   # offline, for ever

Node 0 does not run: its result is built from the parameters the task
already holds. Node 0 is the extractor, it is a model call, and it has
nothing to add to a task whose parameters were extracted once already.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

from friday.kernel.config import load_config
from friday.kernel.dag.task_types import BootContext
from friday.kernel.outbox import DEFAULT_APPROVER, DEFAULT_SENDER
from friday.kernel.plugin_host import TaskTypeAPI
from friday.sdk.actions import Ask, HandOver, Reply
from friday.kernel.domain.conversation import ConversationId
from friday.sdk.workflow import DAG, Deps as DAGDeps, NodeRun
from plugins.devops.config import load_devops_config
from plugins.devops.graph import build_devops_dag, build_log_sources
from plugins.devops.graph.deps import ApiIssueDeps
from plugins.devops.params import ApiIssueParams
from plugins.devops.sources.logs import LokiSource, SshKubectlSource
from friday.store.db import Database
from friday.kernel.dag import adapter


from dataclasses import replace as _dc_replace


def _replay_dag(config, *, with_model, reports):
    """The `devops.api_issue` DAG, built through the plugin against a boot
    context — the composition root's job, done here for one replayed case. The
    diagnose model is dropped when `with_model` is false by hiding its agent."""
    devops_cfg = _dc_replace(
        load_devops_config((config.plugin_blocks or {}).get("devops")),
        reports_dir=str(reports),
    )
    whole = config if with_model else _dc_replace(
        config,
        agents={k: v for k, v in config.agents.items() if k != "devops.diagnose"},
    )
    api = TaskTypeAPI(caps=BootContext(config=whole, servers={}), config=devops_cfg)
    return build_devops_dag(api), devops_cfg


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


def answers(runs: list[NodeRun], final: dict[str, Any], *, wall_s: float) -> dict[str, Any]:
    """Ticket 00's questions 3 and 4, off what the run recorded.

    Questions 1 and 2 are not here and cannot be: whether the dossier held
    the decisive line, and whether the cause is right, are the operator's to
    say. This is the half a run can answer about itself, so the half that is
    left is small enough to be worth asking a person.
    """
    found = final.get("find_request_log", {})
    read = final.get("read_failing_code", {})
    thought = final.get("diagnose", {})
    broke = [r for r in runs if r.status in {"error", "timed_out"}]
    return {
        "wall_s": round(wall_s, 1),
        "nodes": [
            {
                "node": r.node,
                # **Not `r.status` alone.** `node_runs` records any `Action`
                # as `ok`, because an `Action` carries no envelope — so a
                # node that ended the whole run with a hand-over reads
                # exactly like one that succeeded and passed the work on.
                # That cost ten minutes of reading the wrong thing the first
                # time this tool was pointed at a broken row.
                "status": _decided(final.get(r.node)) or r.status,
                "ms": r.duration_ms,
                "reason": _said(final.get(r.node)) or r.reason,
            }
            for r in runs
        ],
        "dossier_lines": found.get("kept", 0) if isinstance(found, dict) else 0,
        "lines_offered": found.get("total", 0) if isinstance(found, dict) else 0,
        "source": found.get("source", "—") if isinstance(found, dict) else "—",
        "code_read": bool(read.get("code")) if isinstance(read, dict) else False,
        "diagnosis": thought.get("diagnosis") if isinstance(thought, dict) else None,
        "not_checked": list(thought.get("not_checked", []))
        if isinstance(thought, dict)
        else [],
        # Question 4. A node that ended `error` or `timed_out` is a seam that
        # broke; an `empty` or `skipped` one did its job and had nothing.
        "broke": [f"{r.node}: {r.reason}" for r in broke],
    }


def _decided(result: Any) -> str:
    """What a node that answered with an `Action` did, in a word."""
    return {HandOver: "handed over", Ask: "asked", Reply: "replied"}.get(
        type(result), ""
    )


def _said(result: Any) -> str:
    if isinstance(result, HandOver):
        return result.reason
    if isinstance(result, (Ask, Reply)):
        return result.text
    return ""


def render(
    task_id: int | str, found: dict[str, Any], report: Path | None,
    *, held: bool | None = None,
) -> str:
    lines = [
        f"=== task {task_id}: {found['wall_s']}s, "
        f"{len(found['nodes'])} nodes, "
        f"{'one model call' if found['diagnosis'] is not None else 'no model call'} ===",
    ]
    for node in found["nodes"]:
        lines.append(
            f"  {node['node']:<18} {node['status']:<8} {node['ms']:>6}ms  "
            f"{node['reason'][:78]}"
        )
    lines += [
        "",
        f"3. dossier: {found['dossier_lines']} of {found['lines_offered']} "
        f"lines from `{found['source']}`; code read: {found['code_read']}",
        f"4. seams that broke: {', '.join(found['broke']) or 'none'}",
        "",
    ]
    if found["diagnosis"]:
        lines.append(f"2. cause: {found['diagnosis'].get('cause', '')}")
        lines.append(
            f"   {found['diagnosis'].get('confidence')} · "
            f"{'conclusive' if found['diagnosis'].get('conclusive') else 'not conclusive'}"
        )
    else:
        lines.append("2. no diagnosis this run")
    for line in found["not_checked"]:
        lines.append(f"   not checked: {line}")
    lines += [
        "",
        # A captured case wrote down the decisive line when it was captured,
        # so question 1 is answered here rather than asked here. Only a case
        # that did not say gets the question.
        f"1. the line you called decisive is in the dossier: {held}"
        if held is not None
        else "1. did the dossier hold the line you call decisive? "
        + ("read the report and say" if report else "no report was written"),
        f"   {report}" if report else "",
    ]
    return "\n".join(lines)


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

    #: `{"window": <answer>, "narrowed": <answer>}`, each exactly what the
    #: Loki tool returned on the day.
    reads: dict[str, Any]

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        # Which of the two reads this is, off the query the source built.
        # A captured case holds one window, so a graph that widens sees the
        # same lines again — true to what was captured and not pretending to
        # be more.
        narrowed = "|=" in str(arguments.get("query", ""))
        return json.dumps(self.reads["narrowed" if narrowed else "window"])


#: Everything a captured case may say that is not a parameter of the issue
#: itself. Named here so anything else is a mistake rather than a silence.
CASE_KEYS = frozenset({
    "id", "channel_id", "reported_at", "reads", "source",
    # What the operator said was true, used by `evals/run_api_issue_eval.py`
    # and by nothing at run time. `cause_mentions` is the tokens any correct
    # answer must contain; `conclusive` is whether the evidence really did
    # settle it, which is a judgement about their system and not about the
    # model.
    "decisive", "cause", "cause_mentions", "conclusive", "captured", "notes",
})


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
    known = {f.name for f in fields(ApiIssueParams)}
    unknown = sorted(set(case) - known - CASE_KEYS)
    if unknown:
        raise ValueError(
            f"{unknown} is not a parameter of api_issue nor part of a case. "
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
        # The narrowed read is the one that greps; see the source itself.
        return str(self.reads["narrowed" if "grep -F" in remote else "window"])


async def run_captured(case: dict, *, with_model: bool, into: Path):
    """One captured case through the graph, returning what the run produced.

    Split out of `replay_captured` so the eval runner and the command line
    share one path. A second way to run a case is a second way for a score
    to disagree with what the operator sees.
    """
    config = load_config()
    into.mkdir(parents=True, exist_ok=True)
    db = await Database.connect(str(copy_aside(Path(config.database_path), into)))
    try:
        reports = into / "reports"
        dag, _devops_cfg = _replay_dag(config, with_model=with_model, reports=reports)
        params = case_params(case)
        name, source = canned_source(case)
        deps = ApiIssueDeps(
            task=SimpleNamespace(
                id=case["id"],
                conversation=ConversationId("discord", str(case["channel_id"])),
                params=params,
                created_at=datetime.fromisoformat(case["reported_at"]),
            ),
            db=db,
            sender=DEFAULT_SENDER,
            approver=DEFAULT_APPROVER,
            log_sources={name: source},
        )
        final, runs, wall_s = await _run_on_adapter(
            dag,
            deps=deps,
            seed={"prepare": ApiIssueParams(**params)},
            system_db=into / "replay-system.db",
            wfid=f"replay-{case['id']}",
        )
        return final, runs, wall_s, reports
    finally:
        await db.close()


async def replay_captured(case_path: Path, *, with_model: bool, into: Path) -> int:
    """One captured case, through the same graph the live one runs."""
    case = json.loads(case_path.read_text())
    final, runs, wall_s, reports = await run_captured(
        case, with_model=with_model, into=into
    )
    found = answers(runs, final, wall_s=wall_s)
    written = next(iter(sorted(reports.glob(f"{case['id']}.md"))), None)
    # The half a captured case can score by itself: the operator wrote down
    # the line they call decisive when they captured it, so question 1 stops
    # being a question put to a person every run.
    decisive = case.get("decisive")
    held = None
    if decisive:
        dossier = final.get("find_request_log", {})
        held = decisive in (
            dossier.get("dossier", "") if isinstance(dossier, dict) else ""
        )
    print(render(case["id"], found, written, held=held))
    if decisive and not held:
        print(f"   missing: {decisive[:120]}")
    if case.get("cause"):
        print(f"\n2. you said the cause is: {case['cause']}")
    return 0


async def replay(task_id: int, *, with_model: bool, into: Path) -> int:
    config = load_config()
    into.mkdir(parents=True, exist_ok=True)
    db = await Database.connect(str(copy_aside(Path(config.database_path), into)))
    try:
        task = await db.task(task_id)
        if task is None:
            print(f"no task {task_id}", file=sys.stderr)
            return 1
        if task.type != "devops.api_issue":
            print(f"task {task_id} is {task.type}, not devops.api_issue", file=sys.stderr)
            return 1

        reports = into / "reports"
        dag, devops_cfg = _replay_dag(config, with_model=with_model, reports=reports)
        deps = ApiIssueDeps(
            task=SimpleNamespace(
                id=task.id,
                conversation=task.conversation,
                params=task.params,
                created_at=task.created_at,
            ),
            db=db,
            sender=DEFAULT_SENDER,
            approver=DEFAULT_APPROVER,
            log_sources=build_log_sources(devops_cfg, {}),
        )
        final, runs, wall_s = await _run_on_adapter(
            dag,
            deps=deps,
            seed={"prepare": ApiIssueParams(**task.params)},
            system_db=into / "replay-system.db",
            wfid=f"replay-task-{task_id}",
        )
        found = answers(runs, final, wall_s=wall_s)
        written = next(iter(sorted(reports.glob(f"{task_id}.md"))), None)
        print(render(task_id, found, written))
        return 0
    finally:
        await db.close()


def main() -> int:
    load_dotenv()
    # The api_issue graph reads memory (environment/route/service rows) through
    # the store, which reads the kind registry (ticket 12); fill it before a run.
    from friday.kernel.memory.registry import register_all_memory_kinds

    register_all_memory_kinds()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "task_id", type=int, nargs="?",
        help="a task already in the store. Needs its back end reachable.",
    )
    parser.add_argument(
        "--case", type=Path, default=None,
        help="a captured case: its parameters and the log its back end "
             "returned, in one file. Replayable offline and for ever, which "
             "a dev pod's log is not.",
    )
    parser.add_argument(
        "--diagnose", action="store_true",
        help="run the model too. Off by default: question 1 is about the "
             "distillation rule and costs nothing to ask.",
    )
    parser.add_argument(
        "--into", type=Path, default=None,
        help="where the copy and the report go. A temporary directory by "
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
