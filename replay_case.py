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
question about `friday/dag/api_issue/distil.py`, a pure function. It can be
asked and re-asked for nothing. Only turn the model on once the dossier is
right, or pay for reasoning over a dossier nobody has checked.

    uv run replay_case.py 6                 # no model, ~5s
    uv run replay_case.py 6 --diagnose      # one model call
    uv run replay_case.py 6 --into ./out    # where the report goes

Node 0 does not run: its result is built from the parameters the task
already holds. Node 0 is the extractor, it is a model call, and it has
nothing to add to a task whose parameters were extracted once already.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
import time
from pathlib import Path
from tempfile import mkdtemp
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

from friday.config import load_config
from friday.dag.api_issue import build_api_issue_dag, build_diagnose_harness, build_log_sources
from friday.dag.engine import DAGDeps, DAGRunner, DAGState, NodeRun
from friday.domain.actions import Ask, HandOver, Reply
from friday.domain.models import PARAMS
from friday.store.db import Database


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


def answers(runs: list[NodeRun], final: DAGState, *, wall_s: float) -> dict[str, Any]:
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


def render(task_id: int, found: dict[str, Any], report: Path | None) -> str:
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
        "1. did the dossier hold the line you call decisive? "
        + ("read the report and say" if report else "no report was written"),
        f"   {report}" if report else "",
    ]
    return "\n".join(lines)


async def replay(task_id: int, *, with_model: bool, into: Path) -> int:
    config = load_config()
    into.mkdir(parents=True, exist_ok=True)
    db = await Database.connect(str(copy_aside(Path(config.database_path), into)))
    try:
        task = await db.task(task_id)
        if task is None:
            print(f"no task {task_id}", file=sys.stderr)
            return 1
        if task.type not in PARAMS or task.type != "api_issue":
            print(f"task {task_id} is {task.type}, not api_issue", file=sys.stderr)
            return 1

        reports = into / "reports"
        dag = build_api_issue_dag(
            diagnose=config.agents.get("diagnose") if with_model else None,
            diagnose_harness=build_diagnose_harness(config) if with_model else None,
            reports_dir=reports,
        )
        runs: list[NodeRun] = []

        async def record(run: NodeRun) -> None:
            runs.append(run)

        deps = DAGDeps(
            task=SimpleNamespace(
                id=task.id,
                conversation=task.conversation,
                params=task.params,
                created_at=task.created_at,
            ),
            db=db,
            extra={"log_sources": build_log_sources(config, {})},
        )
        state = DAGState.empty().with_result(
            "prepare", PARAMS[task.type](**task.params)
        )
        runner = DAGRunner(dag, deps=deps, state=state, on_node_run=record)

        started = time.monotonic()
        final = await runner.run()
        found = answers(runs, final, wall_s=time.monotonic() - started)
        written = next(iter(sorted(reports.glob(f"{task_id}.md"))), None)
        print(render(task_id, found, written))
        return 0
    finally:
        await db.close()


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id", type=int)
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
    into = args.into or Path(mkdtemp(prefix="friday-replay-"))
    return asyncio.run(replay(args.task_id, with_model=args.diagnose, into=into))


if __name__ == "__main__":
    raise SystemExit(main())
