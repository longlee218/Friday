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
from pathlib import Path
from tempfile import mkdtemp

from dotenv import load_dotenv

from evals.api_issue import Scored, report, score

CASES = Path("data/cases")


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
    from replay_case import run_captured

    scored: list[Scored] = []
    for case in found:
        final, _runs, _wall, _reports = await run_captured(
            case, with_model=True, into=into / str(case.get("id", "case"))
        )
        thought = final.get("diagnose", {})
        diagnosis = (
            thought.get("diagnosis") if isinstance(thought, dict) else None
        )
        scored.append(score(case, diagnosis))
    return scored


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
