"""Running a registered eval on Pydantic Evals — the one module that imports
`pydantic_evals` (one module per adopted library; guarded by a test).

Sequential (`max_concurrency=1`): a scripted model replays by call order, and
an agent's harness serialises its runs anyway, so running cases side by side
would only make a run non-deterministic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from friday.sdk.eval import EvalCase, EvalSpec

__all__ = ["Ran", "outputs_or_raise", "run"]


@dataclass
class _Check(Evaluator):
    """One of the spec's `checks`, as the framework's per-case evaluator, so
    its report shows it per row. The case travels in the inputs."""

    label: str
    check: Callable[[EvalCase, Any], float | bool]

    def get_default_evaluation_name(self) -> str:
        return self.label

    def evaluate(self, ctx: EvaluatorContext[EvalCase, Any]) -> float | bool:
        return self.check(ctx.inputs, ctx.output)


@dataclass(frozen=True)
class Ran:
    """One run: `(case, output)` pairs in the order `spec.cases()` returned
    them, and the framework's per-case table — one column per check."""

    results: list[tuple[EvalCase, Any]]
    table: str


async def run(
    spec: EvalSpec,
    task: Callable[[EvalCase], Awaitable[Any]],
    *,
    progress: bool = False,
) -> Ran:
    """Every case of `spec` through `task`. A case whose task raised is an
    error (`outputs_or_raise`), never a quietly smaller set; two cases with
    one name are refused, because the report could not tell them apart."""
    cases = list(spec.cases())
    names = [c.name for c in cases]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise ValueError(f"{spec.name}: more than one case is named {', '.join(twice)}")
    dataset = Dataset[EvalCase, Any, None](
        name=spec.name,
        cases=[Case(name=c.name, inputs=c, expected_output=c.expected) for c in cases],
        evaluators=[_Check(label, check) for label, check in spec.checks.items()],
    )
    report = await dataset.evaluate(task, max_concurrency=1, progress=progress)
    return Ran(
        results=list(zip(cases, outputs_or_raise(report))),
        table=report.render(include_durations=False),
    )


def outputs_or_raise(report: Any) -> list[Any]:
    """Every case's output — but only once every case actually ran.

    `pydantic-evals` catches a task exception, parks the row in
    `report.failures`, and returns the successes. For a scored regression net
    that is the wrong default: a transient provider error on one row would
    quietly shrink the denominator, and every number would be computed over a
    smaller set with nothing saying so.
    """
    if report.failures:
        failed = "; ".join(f"{f.name}: {f.error_message}" for f in report.failures)
        raise RuntimeError(
            f"{len(report.failures)} eval case(s) failed to run, so the set "
            f"scored would be smaller than the set given — {failed}"
        )
    return [case.output for case in report.cases]
