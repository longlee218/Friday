"""Run every captured case through the graph, with the model on, and score it.

    uv run python -m evals.run_api_issue_eval

**The set is not in this repository, and that is deliberate.** A captured
case holds the raw answers a log back end gave, and those lines carry
`userId`, `ip` and `deviceId`. They live under `data/cases/`, which is
gitignored, so this reads a directory rather than a `.jsonl` checked in
beside `triage.jsonl`. What is in the repository is how a case is scored,
which is the part anybody needs to argue with.

Each case carries its own labels, written when it was captured and confirmed
by the operator:

    "decisive":       a substring of the log line they call decisive
    "cause":          the true cause, in their words
    "cause_mentions": the tokens any correct answer must contain
    "conclusive":     whether the evidence really did settle it

The same graph the live task runs, through `replay_case.run_captured`. A
second way to run a case is a second way for a score to disagree with what
the operator sees.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

from dotenv import load_dotenv
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from evals import outputs_or_raise
from evals.api_issue import Scored, report, score

CASES = Path("data/cases")


@dataclass
class CauseFound(Evaluator):
    """The framework's per-case view: 1.0 when the diagnosis carried every token
    the operator said a correct answer must contain. `report` below is where the
    three domain numbers and the rows behind them live."""

    def evaluate(self, ctx: EvaluatorContext[dict, Scored]) -> float:
        return 1.0 if ctx.output.cause_found else 0.0


def cases(directory: Path = CASES) -> list[dict]:
    """Every captured case, in a stable order so two runs compare."""
    return [
        json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))
    ]


def unlabelled(found: list[dict]) -> list[str]:
    """Cases nobody has told the truth about yet.

    Named rather than skipped: a case in the directory with no labels scores
    zero for the cause, which reads as the model failing when it is the set
    that is unfinished.
    """
    return [
        str(c.get("id", "?")) for c in found if not (c.get("cause_mentions") or ())
    ]


async def run(found: list[dict], *, into: Path) -> list[Scored]:
    """Run every captured case through the graph and score it, through a
    `pydantic-evals` `Dataset`. The framework runs the cases and holds the
    per-case report; the task replays one case with the model on and scores it,
    and the three domain numbers stay in `report`. **Sequential**: each replay
    is a full graph run against the live provider, and running them side by side
    would only interleave their output directories for nothing.
    """
    from replay_case import run_captured

    async def diagnose(case: dict) -> Scored:
        final, _runs, _wall, _reports = await run_captured(
            case, with_model=True, into=into / str(case.get("id", "case"))
        )
        thought = final.get("diagnose", {})
        diagnosis = (
            thought.get("diagnosis") if isinstance(thought, dict) else None
        )
        return score(case, diagnosis)

    dataset = Dataset[dict, Scored, None](
        name="api_issue",
        cases=[
            Case(name=str(case.get("id", f"case-{i}")), inputs=case, expected_output=None)
            for i, case in enumerate(found)
        ],
        evaluators=[CauseFound()],
    )
    report_ = await dataset.evaluate(diagnose, max_concurrency=1, progress=False)
    # A case whose replay raised is dropped by the framework; surface it rather
    # than score a smaller set than was given (see `outputs_or_raise`).
    return outputs_or_raise(report_)


async def main() -> int:
    load_dotenv()
    found = cases()
    if not found:
        print(f"no cases in {CASES}/ — capture one first", file=sys.stderr)
        return 1
    missing = unlabelled(found)
    if missing:
        print(
            f"unlabelled, and scored as failures until somebody says what was "
            f"true: {', '.join(missing)}",
            file=sys.stderr,
        )
    scored = await run(found, into=Path(mkdtemp(prefix="friday-eval-")))
    print(report(scored))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
