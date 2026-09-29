"""Run a registered eval against the live provider.

    uv run run_eval.py                               # list the registered evals
    uv run run_eval.py core.triage                   # score triage
    uv run run_eval.py core.triage --add-confirmed   # add ✅-marked verdicts as cases
    uv run run_eval.py core.planner                  # score the Planner's plan shapes
    uv run run_eval.py backend.trace_problem             # replay data/cases/ and score
    FRIDAY_DB=/path/to/db uv run run_eval.py core.triage

Composition, like `run_agent.py`: a plugin registers an eval's cases and
checks (`api.eval`), the core runs them on Pydantic Evals
(`friday.kernel.evals.run`), and this builds the task each case goes through —
the one piece that needs a live agent. Not run by the suite: every case calls
the configured provider, so a run costs money.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from tempfile import mkdtemp

from dotenv import load_dotenv

from friday.kernel.config import load_config
from friday.kernel.evals.run import run
from friday.kernel.plugin_host import load_plugins


async def _triage_task(config):
    from friday.kernel.evals.triage import build_live_triage, build_task

    return build_task(await build_live_triage(config))


async def _planner_task(config):
    """The Planner's memory is a throwaway store, which checks each row's
    kind against the registry — so the kinds are registered first, as
    `run_agent.py` does at boot."""
    from friday.kernel.evals.planner import build_task
    from friday.kernel.memory.registry import register_all_memory_kinds

    register_all_memory_kinds(config)
    return build_task(config, load_plugins(config).registry)


async def _trace_problem_task(config):
    """Temporary until build-the-spine ticket 14: a case is replayed through
    the DAG, which only the composition root can reach."""
    from plugins.backend.evals.trace_problem import cases, unlabelled
    from replay_case import run_captured

    missing = unlabelled(cases())
    if missing:
        print(
            f"unlabelled, and scored as failures until somebody says what was "
            f"true: {', '.join(missing)}",
            file=sys.stderr,
        )
    into = Path(mkdtemp(prefix="friday-eval-"))

    async def diagnose(case):
        final, _runs, _wall, _reports = await run_captured(
            case.inputs, with_model=True, into=into / case.name
        )
        thought = final.get("diagnose", {})
        return thought.get("diagnosis") if isinstance(thought, dict) else None

    return diagnose


#: How each eval's cases are run. A registered eval with no entry here cannot
#: be run yet, and says so.
TASKS = {
    "core.triage": _triage_task,
    "core.planner": _planner_task,
    "backend.trace_problem": _trace_problem_task,
}


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", nargs="?", help="the eval to run")
    parser.add_argument(
        "--add-confirmed",
        action="store_true",
        help="core.triage only: add operator-marked verdicts as new cases, then stop",
    )
    args = parser.parse_args(argv)

    load_dotenv()  # `run_agent.py`'s own first step — secrets from .env.
    config = load_config()
    evals = load_plugins(config).registry.evals()
    if args.name not in evals:
        for name, spec in sorted(evals.items()):
            print(f"{name:<22} {spec.description}")
        return 0 if args.name is None else 1

    spec = evals[args.name]
    if args.add_confirmed:
        return await _add_confirmed(args.name, config)
    if args.name not in TASKS:
        print(
            f"{args.name} is registered but has no task to run it yet", file=sys.stderr
        )
        return 1
    ran = await run(spec, await TASKS[args.name](config), progress=True)
    if not ran.results:
        print(f"{args.name} has no cases", file=sys.stderr)
        return 1
    print(ran.table)
    print(spec.report(ran.results))
    return 0


async def _add_confirmed(name: str, config) -> int:
    if name != "core.triage":
        print("--add-confirmed is core.triage's only", file=sys.stderr)
        return 1
    from friday.kernel.evals.triage import live_db
    from friday.kernel.evals.triage_set import add_confirmed

    async with live_db(config) as db:
        written = await add_confirmed(db, config)
    for path in written:
        print(f"added {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
