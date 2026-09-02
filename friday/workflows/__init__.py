"""Fill in what the text carries, check it — the engine every graph's entry
node runs, and the answer for a type with nothing past that node.

`prepare` extracts and validates; `plan_by_required_parameters` is what a
type with no investigation of its own says once `prepare` has nothing left to
ask about — "no workflow for this yet". Neither is called directly by the
loop any more: every task type is a graph (D1), so both are reached through
`friday.dag.prepare.prepare_node`, which every graph's node 0 is built from.
There used to be a third thing here — a registry of per-type planner
functions, some of them agentic — and ticket 33 emptied it, ticket 04 removed
the branch that read what was left of it: a dispatcher with nothing to
dispatch to is not extensibility, it is a second way to do what the graphs
already do.

`Ask`, `Reply` and `Park` live in `friday.domain.actions` — they are the
vocabulary every path speaks, graphs included, so neither this module nor the
graph engine defines them.

`prepare` also honours a `Clarify` the extractor produced (ticket 05) — but
only once its own structural and semantic checks have nothing left to say,
and only for whichever of the model's named fields the fill actually left
blank. Code is the floor; the model can ask for more than code requires, not
argue its way past what code rejects.
"""

from __future__ import annotations

from dataclasses import fields
from typing import get_args, get_type_hints

from friday.domain.actions import Action, Ask, Park, Reply
from friday.domain.models import (
    AccessRequestParams,
    ApiIssueParams,
    DocQuestionParams,
    Params,
)
from friday.domain.validation import Problem, validate

#: Imported lazily inside `plan()` would only save a sys.modules lookup; it
#: does not break a cycle (extraction.py imports harness and models, not
#: workflows). Keep it here so the import graph is one read of the file.
from friday.extraction import Clarify, extract as _extract

__all__ = [
    "Action",
    "Ask",
    "MODEL_AUTHORED",
    "PARAMS",
    "Park",
    "Reply",
    "plan_by_required_parameters",
    "prepare",
]


PARAMS: dict[str, type] = {
    "api_issue": ApiIssueParams,
    "access_request": AccessRequestParams,
    "doc_question": DocQuestionParams,
}

#: Written by the model about the message, not supplied by the person who sent
#: it. Asking someone for a summary of their own message is nonsense. Public —
#: `friday.extraction` reads it too, to build `ask_clarification`'s closed
#: enum of a type's *askable* fields.
MODEL_AUTHORED = frozenset({"summary"})

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
    """Fill the parameters in, then check them. Node 0 of every graph.

    Returns the parameters to work with, and an `Ask` when they are not fit to
    work with at all — a field missing, or one whose value the type's rules
    reject.

    **This is what every graph's entry node runs** (`friday.dag.prepare`), not
    a step ahead of one. It used to be the first two steps of a `plan()` that
    ran before the graph route was even chosen — which meant a task type with
    a graph, `api_issue`, the only type that had an extractor *and* rules,
    got neither: its `_RULES` were unreachable in production, its configured
    extractor could never run, and a `correlation_id` of "not-a-uuid" reached
    the graph, looked findable, and parked to the operator instead of asking
    the reporter to resend it. Nothing failed; it just quietly stopped
    happening. Ticket 03 moved this inside the graph so there is nowhere left
    for it to be skipped from.

    Extraction runs before validation on purpose: validation is what stops a
    hallucinated field from being believed, so it has to see what the extractor
    produced and not only what triage wrote.

    **Code is still the floor** (D12). The extractor may call
    `ask_clarification` — it just read the whole thread, and may catch an
    ambiguity no structural rule does — but a value the type's own rules
    reject is challenged with the code template regardless of what the model
    asked about instead. A model's question is honoured only once code has
    nothing to say and only for fields the fill actually left blank: asking
    again for something already answered is not a question this exists to ask.
    """
    clarify: Clarify | None = None
    if text is not None:
        extracted, clarify = await _extract(task_type, text)
        if extracted is not None:
            params = _fill(params, extracted)

    problems = _problems(params)
    if problems:
        return params, Ask(_question(problems))

    if clarify is not None:
        still_missing = tuple(f for f in clarify.fields if not getattr(params, f, None))
        if still_missing:
            return params, Ask(_question_from_clarify(Clarify(still_missing, clarify.because)))

    return params, None


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
    not required; a field the model always writes (see `MODEL_AUTHORED`) is
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
        and f.name not in MODEL_AUTHORED
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


def _question_from_clarify(clarify: Clarify) -> str:
    """Render a `Clarify` the same shape `_question` renders `Problem`s —
    content for the Responder to write from, not a sentence to send verbatim.
    Every reporter-facing Ask goes through the Responder (`WorkflowRunner._say`)
    before it is ever sent; this only has to say what needs asking.
    """
    parts = [_ASKED_AS.get(f, f"the {f.replace('_', ' ')}") for f in clarify.fields]
    question = "Could you tell me " + " and ".join(parts) + "?"
    return f"{question} ({clarify.because})" if clarify.because else question



