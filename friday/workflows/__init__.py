"""What to do about a task, decided by ordinary branching.

Deterministic on purpose. These rules are inspectable, free to run, and identical
every time — which is what makes the first weeks of logs worth reading. A
workflow is promoted to something agentic per type, once the deterministic one
has proven itself.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, fields
from typing import get_args, get_type_hints

from friday.models import (
    AccessRequestParams,
    ApiIssueParams,
    DocQuestionParams,
    Params,
)

__all__ = [
    "Action",
    "Ask",
    "COLLABORATORS",
    "PARAMS",
    "Park",
    "Reply",
    "plan",
    "plan_api_issue",
    "planner",
]


@dataclass(frozen=True, slots=True)
class Ask:
    """Ask the reporter for something. The text is ready to send."""

    text: str


@dataclass(frozen=True, slots=True)
class Reply:
    """An answer. Unlike an `Ask`, this waits for approval.

    The asymmetry is the point: asking for a correlationId costs a question if
    it is wrong, and asserting a cause costs the operator's credibility with
    their own team.
    """

    text: str


@dataclass(frozen=True, slots=True)
class Park:
    """Nothing can be done automatically. A human picks it up."""

    reason: str


Action = Ask | Reply | Park


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


#: What a planner may ask to be handed, by name. A closed list, checked at
#: import — three names is not worth a dependency-injection container, and the
#: alternative is every planner accepting collaborators it does not use.
COLLABORATORS = ("agent",)

_PLANNERS: dict[str, "Planner"] = {}


def planner(task_type: str, *, into: dict | None = None):
    """Register a planner for a task type.

    The reflection happens **here**, once, at import. That is Starlette's answer
    to the same question — it decides sync-versus-async when a route is
    registered rather than on every request — and FastAPI's, which resolves a
    signature in the route's constructor so a bad one fails at import instead of
    at three in the morning.

    Two things therefore fail loudly rather than silently: a task type that does
    not exist, which used to fall through to the generic rule with nobody able
    to see why their planner never ran; and asking for a collaborator that
    cannot be supplied.

    The function is returned unchanged, so a deterministic planner is still a
    plain function anyone can call in a test without a model or a database.
    """
    if task_type not in PARAMS:
        raise KeyError(
            f"no such task type {task_type!r}; known types are {sorted(PARAMS)}"
        )

    def register(fn):
        (_PLANNERS if into is None else into)[task_type] = _adapt(fn)
        return fn

    return register


def _adapt(fn):
    """Wrap a planner so every one of them is called the same way.

    Called with the parameters, plus whichever collaborators it names — so a
    planner that decides by branching declares nothing it does not use, and
    does not become a coroutine to keep company with one that does.
    """
    taken = list(inspect.signature(fn).parameters)[1:]
    unknown = [name for name in taken if name not in COLLABORATORS]
    if unknown:
        raise TypeError(
            f"planner {fn.__name__!r} asks for {unknown}, which nothing supplies; "
            f"available: {list(COLLABORATORS)}"
        )

    async def call(params, **available):
        result = fn(params, **{name: available[name] for name in taken})
        return await result if inspect.isawaitable(result) else result

    call.adapted = True
    return call


async def plan(
    task_type: str,
    params: Params,
    *,
    agent=None,
    planners: dict | None = None,
) -> Action:
    """What to do about a task.

    A planner that reads an external store is where a workflow becomes agentic,
    per task type and on evidence. One that decides by branching is a pure
    function and stays one.

    `planners` overrides the registry, which is how a step is tried before it is
    registered and tested without reaching anything.
    """
    if planners is not None and task_type in planners:
        # Adapted here rather than at registration, so trying a step out takes
        # exactly the shape it will finally be written in — sync or async, with
        # or without collaborators.
        found = planners[task_type]
        if not getattr(found, "adapted", False):
            found = _adapt(found)
        return await found(params, agent=agent)
    found = _PLANNERS.get(task_type)
    if found is None:
        return plan_by_required_parameters(task_type, params)
    return await found(params, agent=agent)


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


# ---- the planners ----------------------------------------------------
#
# Last, because a planner registers itself as it is defined and the machinery
# that registers it has to exist first.

@planner("api_issue")
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
