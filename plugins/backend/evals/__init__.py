"""The backend's evals, registered with `api.eval`."""

from __future__ import annotations

from plugins.backend.evals.trace_problem import TRACE_PROBLEM_EVAL

__all__ = ["EVALS"]

EVALS = (TRACE_PROBLEM_EVAL,)
