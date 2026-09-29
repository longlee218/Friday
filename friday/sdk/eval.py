"""The eval declaration: a named set of cases and how each result is judged.

Pure data, no third-party import: the core runs a registered `EvalSpec` on
Pydantic Evals (`friday.kernel.evals.run`), so a plugin declares its own evals
without naming the kernel or the library. What a case is run *through* — the
task — is not here: the composition root builds it (`run_eval.py`), because
running a case needs the live agent, which a plugin cannot construct.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = ["EvalCase", "EvalSpec"]


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One case: its `name` (unique within the eval), what the task is given,
    and what a correct answer is, if the eval has one."""

    name: str
    inputs: Any
    expected: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvalSpec:
    """A named eval a plugin registers (`api.eval`).

    `cases` reads the set when the eval runs, not at registration. `checks`
    score one case's output — each shows per row in the run's report; the
    value is a number or a bool. `report` turns every `(case, output)` pair
    into the text the operator reads: the aggregate numbers and the rows
    behind them.
    """

    name: str
    description: str
    cases: Callable[[], Sequence[EvalCase]]
    checks: Mapping[str, Callable[[EvalCase, Any], float | bool]]
    report: Callable[[Sequence[tuple[EvalCase, Any]]], str]
