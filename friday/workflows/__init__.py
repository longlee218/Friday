"""What to do about a task, when there is only one decision to make.

The simple path: fill in what the text carries, check it, and ask for whatever
is wrong or missing. Deterministic and free to run, which is what makes the
first weeks of logs worth reading.

A task type that needs more than one decision gets a graph instead
(`friday/dag/`), and the runner routes to that first. There used to be a third
thing here — a registry of per-type planner functions, some of them agentic —
and ticket 33 emptied it: `api_issue` was its only entry and became a graph.
A dispatcher with nothing to dispatch to is not extensibility, it is a second
way to do what the graphs already do, so it is gone.

`Ask`, `Reply` and `Park` stay: they are the vocabulary every path speaks,
graphs included.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import get_args, get_type_hints

from friday.models import (
    AccessRequestParams,
    ApiIssueParams,
    DocQuestionParams,
    Params,
)
from friday.validation import Problem, validate

#: Imported lazily inside `plan()` would only save a sys.modules lookup; it
#: does not break a cycle (extraction.py imports harness and models, not
#: workflows). Keep it here so the import graph is one read of the file.
from friday.extraction import extract as _extract

__all__ = [
    "Action",
    "Ask",
    "PARAMS",
    "Park",
    "Reply",
    "plan",
    "prepare",
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
    #: The cross-field rule's sentinel. Either field answers it, so the
    #: phrasing names both — asking for "the correlation_id or curl" would be
    #: reading a rule out loud instead of asking a question.
    "_traceable": "the correlationId, or the curl you used",
    "question": "what you would like to know",
    "permission": "what access you need",
    "doc_ref": "which document you mean",
}


async def prepare(
    task_type: str,
    params: Params,
    *,
    text: str | None = None,
) -> tuple[Params, Action | None]:
    """Fill the parameters in, then check them. Before any route is chosen.

    Returns the parameters to work with, and an `Ask` when they are not fit to
    work with at all — a field missing, or one whose value the type's rules
    reject.

    **This runs ahead of the graph, not inside the branch that has no graph.**
    It used to be the first two steps of `plan()`, which meant a task type with
    a graph — `api_issue`, the only type that has an extractor *and* rules —
    got neither. Its `_RULES` were unreachable in production, its configured
    extractor could never run, and a `correlation_id` of "not-a-uuid" reached
    the graph, looked findable, and parked to the operator instead of asking
    the reporter to resend it. Nothing failed; it just quietly stopped
    happening.

    Extraction runs before validation on purpose: validation is what stops a
    hallucinated field from being believed, so it has to see what the extractor
    produced and not only what triage wrote.
    """
    if text is not None:
        extracted = await _extract(task_type, text)
        if extracted is not None:
            params = _fill(params, extracted)

    problems = _problems(params)
    return params, Ask(_question(problems)) if problems else None


async def plan(
    task_type: str,
    params: Params,
    *,
    text: str | None = None,
) -> Action:
    """The whole of the simple path, for a task type with no graph.

    A type that needs more than one decision gets a graph (`friday/dag/`), and
    the runner routes to that after `prepare` and instead of this.
    """
    params, problem = await prepare(task_type, params, text=text)
    return problem or plan_by_required_parameters(task_type, params)


def plan_by_required_parameters(task_type: str, params: Params) -> Action:
    """Ask for whatever the type says is not optional and is not there, or
    whatever `validate` says is wrong.

    Required-ness is read off the annotations rather than declared a second
    time: `project: str` is required, `doc_ref: str | None` says outright that
    we can manage without it. A list kept by hand would drift from the schema
    the model is actually asked to fill.
    """
    problems = _problems(params)
    if not problems:
        return Park(f"no workflow for {task_type} yet")
    return Ask(_question(problems))


def _problems(params: Params) -> list[Problem]:
    """What is wrong with the params, structural first then semantic.

    Both run from this one place, so no other module has to remember to call
    them. Returns `Problem` objects directly — the previous version stringified
    and parsed back, which lost the structure the caller needs.
    """
    return [*_missing(params), *validate(params)]


def _fill(known: Params, extracted: Params) -> Params:
    """Fill in the blanks. Never rewrite a field that already has a value.

    This is not the old `_merge`. That reconciled two producers — triage
    lifted values out of the message and so did the extractor — and had to
    decide which won. There is one producer now: triage classifies and stops,
    and every field here comes from the extractor.

    What is left is a different guard, for a different failure. A model asked
    the same question twice does not give the same answer, and this runs again
    on every follow-up. Letting the second run rewrite the first cost nineteen
    direct messages about one report, each carrying a differently worded
    summary: a reworded value is a *changed* value, so the graph discarded its
    work and the operator was told again.

    So the first answer for a field stands. A later run may fill what is still
    blank — which is exactly what a follow-up supplying the correlationId is —
    and may not revise what it already said.
    """
    if not isinstance(extracted, type(known)):
        # Defensive: a misregistered extractor cannot silently rewrite a task
        # type's parameters with another type's.
        return known
    from dataclasses import replace as _replace

    return _replace(
        known,
        **{
            f.name: getattr(extracted, f.name)
            for f in fields(extracted)
            if getattr(extracted, f.name) is not None
            and not getattr(known, f.name)
        },
    )


def _missing(params: Params) -> list[Problem]:
    """The structural half of `_problems`: fields that should be there but are not.

    Optional-ness is read off the annotations. A field marked `str | None` is
    not required; a field the model always writes (see `_MODEL_AUTHORED`) is
    not checked here either. The validation engine handles everything else:
    if a value is present but malformed, that is its problem, not this one's.
    """
    optional = {
        name
        for name, hint in get_type_hints(type(params)).items()
        if type(None) in get_args(hint)
    }
    return [
        Problem(field=f.name)
        for f in fields(params)
        if f.name not in optional
        and f.name not in _MODEL_AUTHORED
        and not getattr(params, f.name)
    ]


def _question(problems: list[Problem]) -> str:
    """Render the joined problems as one operator-facing question.

    The field name drives which natural-language form to use; the message is
    appended only when it carries information the form does not (i.e. when it
    came from the validation engine, not the structural check).

    Deduplicates by field: a single field reported by both layers (or by two
    rules in `_RULES`) should not appear twice in the sentence. When the same
    field carries both a structural and a semantic problem, the semantic one
    wins because it carries more information.
    """
    assert problems, "_question called with empty problems"
    seen: dict[str, str] = {}
    for problem in problems:
        existing = seen.get(problem.field, "")
        if existing and problem.message:
            continue
        seen[problem.field] = problem.message
    parts: list[str] = []
    for field, message in seen.items():
        asked_as = _ASKED_AS.get(field, f"the {field.replace('_', ' ')}")
        parts.append(f"{asked_as} ({message})" if message else asked_as)
    return "Could you tell me " + " and ".join(parts) + "?"


#: Rebuilding a task's stored parameters as the type that declares which of
#: them are required.
