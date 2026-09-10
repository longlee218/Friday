"""Node 0, shared by every graph, and the fill-and-validate mechanism it runs.

D2: "extract and validate" is the entrypoint of every graph — see ticket 03
— not a gate in front of one, and not a copy per graph either. Every graph
needs exactly the same shape here: read everything the reporter has said,
fill in the parameters, write them back, check them. One function builds it.

`prepare` and the rest of this module used to live in `friday/workflows/`,
back when a second module still existed to hold a fallback for a type with no
graph. Ticket 09 dissolved it: `prepare_node` was already this module's only
caller of `prepare`, so the mechanism moved to sit beside it rather than
staying behind an import.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, fields
from typing import Any, get_args, get_type_hints

from friday.dag.engine import DAGDeps, DAGState, Node
from friday.agent.harness import Refused
from friday.domain.actions import Action, Ask, HandOver
from friday.domain.models import MODEL_AUTHORED, ExtractionMark, Params
from friday.domain.validation import Problem, asked_as, validate
from friday.extraction import (
    Clarify,
    extract as _extract,
    input_fingerprint,
)
from friday.store.db import estimated_tokens

__all__ = ["plan_by_required_parameters", "prepare", "prepare_node", "prepared_ok"]

log = logging.getLogger(__name__)

#: How `prepare` obtains an extraction. Named because two things implement it:
#: the extraction package's own entry point, and the remembering wrapper node 0
#: puts round it.
_Extract = Callable[..., Awaitable[tuple["Params | None", "Clarify | None"]]]

def prepare_node(
    task_type: str,
    params_cls: type[Params],
    *,
    on_ready: Callable[[Params], Params | Action] | None = None,
    budget_tokens: int | None = None,
) -> Node:
    """Node 0: everything the reporter has said, filled in and checked.

    Runs on every pass — there may be a new message since the last one — and
    is never part of the checkpoint (`_run_dag` excludes it before saving);
    only its *output* decides whether the rest of what was checkpointed is
    still worth keeping.

    `on_ready` is what a graph with nothing past node 0 uses to turn a
    complete, valid set of parameters into its own answer. A type with an
    investigation past this node leaves it `None` and reads `state["prepare"]`
    itself instead.

    `budget_tokens` — board `what-the-room-already-knows`, ticket 08, D5-D7
    — is closed over rather than read from `deps`: one `Harness` per task
    type already serves every conversation the same way, and this is the
    same kind of per-type, not per-call, configuration. `None` (the
    default, and `config.yaml`'s until an operator sets it) means no
    compaction at all — `original_text_for`'s message-count cap, unchanged.
    """

    async def _prepare(state: DAGState, deps: DAGDeps) -> Params | Action:
        known = params_cls(**deps.task.params)
        # A task already on cooldown (two ineffective compactions, D6) is
        # read exactly as if no budget were configured — trying a third
        # time cannot help a single message larger than the budget, and the
        # point of the cooldown is that nothing keeps re-checking that.
        on_cooldown = (
            budget_tokens is not None
            and await deps.db.compaction_on_cooldown(deps.task.id)
        )
        effective_budget = None if on_cooldown else budget_tokens
        text = await deps.db.original_text_for(
            deps.task.id, budget_tokens=effective_budget
        )
        if effective_budget is not None and text is not None:
            if estimated_tokens(text) > effective_budget:
                count = await deps.db.record_ineffective_compaction(deps.task.id)
                log.warning(
                    "task %s: compaction did not bring the build under budget "
                    "(%d/%d estimated tokens, attempt %d)",
                    deps.task.id, estimated_tokens(text), effective_budget, count,
                )
        filled, problem = await prepare(
            task_type,
            known,
            text=text,
            extract=_remembering(deps.db, deps.task.id, params_cls),
            channel_id=deps.task.conversation.channel_id,
            task_id=deps.task.id,
            node="prepare",
        )

        merged = {**deps.task.params, **asdict(filled)}
        if merged != deps.task.params:
            await deps.db.set_task_params(deps.task.id, merged)

        if problem is not None:
            return problem
        return on_ready(filled) if on_ready else filled

    return Node("prepare", _prepare)


def prepared_ok(state: DAGState) -> bool:
    """Whether `prepare` cleared the report to continue past node 0."""
    return not isinstance(state["prepare"], Ask)


async def prepare(
    task_type: str,
    params: Params,
    *,
    text: str | None = None,
    #: How the extraction is obtained. Injected so node 0 can hand in one that
    #: remembers, without this function growing a second path — there is one
    #: place a `Refused` becomes a hand-over and one place a fill happens, and
    #: both stay here whether the answer came from a model or from a mark.
    #:
    #: `None` rather than `_extract` as the default, and resolved at the call:
    #: a default argument binds once at definition, so naming the function here
    #: would have quietly outlived every `monkeypatch.setattr(prepare,
    #: "_extract", ...)` in the suite — two tests went red saying so.
    extract: _Extract | None = None,
    #: Which room this is, so the extractor's prompt can carry what the room
    #: is known to be. A string rather than the resolved context: the store
    #: lives with the extractor, and threading a dict through here would put
    #: this module in the business of reading channel files.
    channel_id: str | None = None,
    #: Whose work this is, for the row the extractor's call becomes. Node 0
    #: is the only step that knows both, and the extractor is the only agent
    #: it runs — so this is where the two meet.
    task_id: int | None = None,
    node: str | None = None,
) -> tuple[Params, Action | None]:
    """Fill the parameters in, then check them. Node 0 of every graph.

    Returns the parameters to work with, and an `Ask` when they are not fit to
    work with at all — a field missing, or one whose value the type's rules
    reject.

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

    **`params` is also what `extract` is told is already known** (board
    `what-the-room-already-knows`, ticket 08, D8) — passed through as
    `known=params`, before this call fills anything further, so the schema
    the model is shown drops what this task's own store already has.
    """
    clarify: Clarify | None = None
    if text is not None:
        try:
            extracted, clarify = await (extract or _extract)(
                task_type, text, known=params,
                channel_id=channel_id, task_id=task_id, node=node,
            )
        except Refused as refusal:
            # A ceiling, not a failure. Falling through would leave the fields
            # unfilled, and the code floor below would then ask the reporter
            # for what they already wrote — the exact failure CLAUDE.md names
            # for a task type with no extractor at all.
            return params, HandOver(str(refusal))
        if extracted is not None:
            params = _fill(params, extracted)

    problems = _problems(params)
    if problems:
        return params, Ask(_question(params, problems))

    if clarify is not None:
        still_missing = tuple(f for f in clarify.fields if not getattr(params, f, None))
        if still_missing:
            return params, Ask(
                _question_from_clarify(
                    params, Clarify(still_missing, clarify.because)
                )
            )

    return params, None


def _remembering(db: Any, task_id: int, params_cls: type[Params]) -> _Extract:
    """`extract`, but it does not pay twice for one set of facts.

    Node 0 re-executes on every pass — it is excluded from the checkpoint on
    purpose, because a reporter who sends the curl three seconds later has to
    be read. What it must not do is call a model when nothing arrived. One task
    in the recorded data has two extractor calls of 1,790 input tokens whose
    prompts share a sha256, seven and a half hours apart: it sat pending across
    a restart, and every pass paid again.

    **The fingerprint is the prompt itself**, asked of the extraction family
    rather than rebuilt here — see `input_fingerprint`. Rebuilding it meant
    node 0 had to know every input the prompt has, and it stopped knowing the
    day one was added.

    **A hit returns the same answer the call would have**, not merely nothing.
    The extracted values are replayed so the same fill happens, and the
    clarification is replayed so the same question is asked — without it a skip
    would turn an `Ask` the extractor raised into "everything needed is here"
    on the next pass, because the fields it asks about are usually the optional
    ones no structural rule challenges.

    Wrapped around the seam rather than folded into `prepare`, so a `Refused`
    is still handled in exactly one place and a mark is never written for a
    call that did not happen.
    """

    async def extract(
        task_type: str, text: str, *, known=None,
        channel_id=None, task_id=None, node=None,
    ):
        # Asked of the extraction family rather than reconstructed here. The
        # reconstruction knew about the field schema and the reporter's text,
        # and ticket 01 gave the prompt a third input it could not see — so an
        # operator who wrote down what a room is got a task that never read
        # it, because the fingerprint had not changed. `known` is ticket 08's
        # fifth: a field getting filled shrinks the schema, which the digest
        # must see move the same way.
        digest = await input_fingerprint(
            task_type, text, known=known, channel_id=channel_id, task_id=task_id
        )
        mark = await db.extraction_mark(task_id)
        if mark is not None and mark.fingerprint == digest:
            log.info(
                "task %s: nothing new to read, reusing the last extraction",
                task_id,
            )
            return (
                params_cls(**mark.params) if mark.params else None,
                Clarify(mark.asked_about, mark.because) if mark.asked_about else None,
            )
        extracted, clarify = await _extract(
            task_type, text, known=known,
            channel_id=channel_id, task_id=task_id, node=node,
        )
        if extracted is None and clarify is None:
            # Nothing to remember, so nothing is written — and the next pass
            # calls again. `(None, None)` is not "the model found nothing":
            # a model that finds nothing still returns a `Params` with every
            # field absent. It is the harness having swallowed a provider
            # error into `last_error`, or output that missed the schema.
            # Marking that would turn one 502 into a task that is never read
            # again, which is the opposite of "a hiccup is retried here".
            #
            # `Refused` never reaches this line — it propagates to `prepare`,
            # which hands over — so a ceiling does not write a mark either.
            log.info("task %s: nothing extracted, not marking", task_id)
            return extracted, clarify
        await db.mark_extraction(
            task_id,
            ExtractionMark(
                fingerprint=digest,
                params=asdict(extracted) if extracted is not None else {},
                asked_about=clarify.fields if clarify is not None else (),
                because=clarify.because if clarify is not None else None,
            ),
        )
        return extracted, clarify

    return extract


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
        # Quoted to the operator verbatim (CONTEXT.md, *Hand-over*), so it has
        # to be true. It used to read "no workflow for {task_type} yet", which
        # stopped being true the day every type got a graph — this *is* that
        # type's graph, running — and said it in the vocabulary ticket 09
        # retired. What actually happened is that there was nothing to ask
        # about and nothing further this type knows how to do (ticket 11).
        return HandOver(
            f"{task_type}: everything needed is here, and there is "
            f"no investigation past this point — over to you"
        )
    return Ask(_question(params, problems))


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


def _question(params: Params, problems: list[Problem]) -> str:
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
        phrase = asked_as(params, field)
        parts.append(f"{phrase} ({message})" if message else phrase)
    return "Could you tell me " + " and ".join(parts) + "?"


def _question_from_clarify(params: Params, clarify: Clarify) -> str:
    """Render a `Clarify` the same shape `_question` renders `Problem`s —
    content for the Responder to write from, not a sentence to send verbatim.
    Every reporter-facing Ask goes through the Responder before it is ever
    sent; this only has to say what needs asking.
    """
    parts = [asked_as(params, f) for f in clarify.fields]
    question = "Could you tell me " + " and ".join(parts) + "?"
    return f"{question} ({clarify.because})" if clarify.because else question
