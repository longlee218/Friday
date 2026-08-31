"""What to do about a task, decided by ordinary branching.

Deterministic on purpose. These rules are inspectable, free to run, and identical
every time — which is what makes the first weeks of logs worth reading. A
workflow is promoted to something agentic per type, once the deterministic one
has proven itself.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import get_args, get_type_hints

from friday.triage import (
    AccessRequestParams,
    ApiIssueParams,
    DocQuestionParams,
    Params,
)

__all__ = ["Action", "Ask", "Park", "PARAMS", "plan", "plan_api_issue"]


@dataclass(frozen=True, slots=True)
class Ask:
    """Ask the reporter for something. The text is ready to send."""

    text: str


@dataclass(frozen=True, slots=True)
class Park:
    """Nothing can be done automatically. A human picks it up."""

    reason: str


Action = Ask | Park


def plan_api_issue(params: ApiIssueParams) -> Action:
    """A report is only actionable once it can be traced.

    A correlation id or a curl is what makes a specific request findable in the
    logs. An environment narrows the search but cannot locate anything on its
    own, so it is asked for alongside — never instead.
    """
    if params.correlation_id or params.curl:
        return Park("has enough to trace")

    wanted = ["the correlationId, or the curl you used"]
    if not params.environment:
        wanted.insert(0, "which environment you're on")
    return Ask(
        "Could you send " + " and ".join(wanted) + "? "
        "I'll trace it from there."
    )


#: Rebuilding a task's stored parameters as the type that declares which of
#: them are required.
PARAMS: dict[str, type] = {
    "api_issue": ApiIssueParams,
    "access_request": AccessRequestParams,
    "doc_question": DocQuestionParams,
}

#: Written by the model about the message, not supplied by the person who sent
#: it. Asking someone for a summary of their own message is nonsense.
_MODEL_AUTHORED = frozenset({"summary"})

#: Field names that read badly as a question. Anything absent falls back to the
#: field name, which is usually fine — "the project", "the permission".
_ASKED_AS = {
    "environment": "which environment you're on",
    "correlation_id": "the correlationId",
    "curl": "the curl you used",
    "question": "what you would like to know",
    "permission": "what access you need",
    "doc_ref": "which document you mean",
}


def plan(task_type: str, params: Params) -> Action:
    """What to do about a task.

    A type with its own workflow gets it. Everything else falls back to the one
    rule that always holds: a task missing something it cannot work without has
    to say so. Silence there is the same failure as a dropped mention — the
    reporter believes they were heard and nothing is happening.
    """
    planner = _PLANNERS.get(task_type)
    if planner is not None:
        return planner(params)
    return plan_by_required_parameters(task_type, params)


def plan_by_required_parameters(task_type: str, params: Params) -> Action:
    """Ask for whatever the type says is not optional and is not there.

    Required-ness is read off the annotations rather than declared a second
    time: `project: str` is required, `doc_ref: str | None` says outright that
    we can manage without it. A list kept by hand would drift from the schema
    the model is actually asked to fill.
    """
    missing = _missing(params)
    if not missing:
        return Park(f"no workflow for {task_type} yet")
    return Ask(_question(missing))


def _missing(params: Params) -> list[str]:
    optional = {
        name
        for name, hint in get_type_hints(type(params)).items()
        if type(None) in get_args(hint)
    }
    return [
        f.name
        for f in fields(params)
        if f.name not in optional
        and f.name not in _MODEL_AUTHORED
        and not getattr(params, f.name)
    ]


def _question(missing: list[str]) -> str:
    wanted = [_ASKED_AS.get(name, f"the {name.replace('_', ' ')}") for name in missing]
    return "Could you tell me " + " and ".join(wanted) + "?"


_PLANNERS = {"api_issue": lambda params: plan_api_issue(params)}
