"""A field extractor per workflow.

Each workflow that needs fields from text declares one. The extractor is a
`Harness` whose schema is the workflow's `Params` type, so the same place that
describes a field describes the rule for filling it. The model may
hallucinate; that is the cost of LLM extraction, and ticket 30's validate
engine catches what it gets wrong.

Registered at startup by `register_extractors`, looked up by task type, and
called by `prepare()`, which is node 0 of every graph. Adding one is registering
a task type (ticket 11) and a block in `config.yaml`, not a change to the
composition root — the types come from the registry the graphs are built from.

An extraction is **one validated object** (board `every-answer-has-a-shape`,
D7): that type's own parameters, plus which of its own fields the extractor
wants the reporter asked about and why. The model has just read everything the
reporter said, and may know something is missing that no structural rule
catches. It names *which* fields, and why — never words, so it cannot be argued
into phrasing that bypasses the operator's voice — and
`friday.kernel.dag.prepare.prepare` turns that into an `Ask` the Responder writes. Code
stays the floor regardless: a field the structural rules reject is challenged
with the code template whether or not the model asked about it.

That request used to be a second tool, `ask_for_fields`, writing into a per-run
capture the caller read back afterwards — so one extraction produced two
answers by two mechanisms and neither was the return value of anything. The
shape is in `friday/kernel/extraction/answer.py` now, and the closed set of field
names the tool's enum gave is still closed.
"""

from __future__ import annotations

import logging
from dataclasses import fields
from typing import TYPE_CHECKING

from friday.kernel.harness.harness import Harness, Refused
from friday.kernel.extraction.answer import Clarify, answer_shape, params_and_clarify
from friday.kernel.extraction.context import FullContext
from friday.kernel.domain.models import MODEL_AUTHORED, Params
from friday.kernel.extraction.prompt import build_input, build_instructions

if TYPE_CHECKING:
    # `friday.kernel.config` sits above the packages because it is read before any of
    # them, so naming it here is a type-checking edge and not an import. It was
    # a string annotation with a `name-defined` suppression on it, and the
    # suppression is the thing that rots: reflowing this signature to take
    # `skills` moved the comment off the line the error is reported on, and
    # mypy went from silent to complaining about a name that had been undefined
    # the whole time.
    from friday.kernel.config import Config

__all__ = [
    "input_fingerprint",
    "Clarify",
    "Extractor",
    "build_extractor",
    "extract",
    "register",
    "register_extractors",
    "registered",
]

log = logging.getLogger(__name__)

#: Task type -> the extractor that fills its parameters. Written by
#: `register()`, read by `extract()` from inside `prepare()`.
_EXTRACTORS: dict[str, "Extractor"] = {}


class Extractor:
    """A workflow's extraction agent, plus the instructions that drive it.

    Constructed by `build_extractor`; called by `extract`. The Harness is held
    but not eagerly run — a plan only invokes the extractor when the params
    actually have a relevant text source, and only after the validate engine
    has decided the params are worth filling in.
    """

    def __init__(
        self,
        harness: Harness,
        params_cls: type[Params],
        name: str,
    ) -> None:
        self._harness = harness
        self._params_cls = params_cls
        self.name = name

    async def would_ask(self, context: FullContext) -> str:
        """The per-call input this would send, without sending it.

        One method so `run` and `input_fingerprint` cannot disagree about
        what the prompt is — which is exactly how the mark came to be
        stale: two places described the same prompt and only one of them
        learned about the room.

        **`context` is gathered once, by node 0, before this is called**
        (board `what-the-room-already-knows`, ticket 15, D26). This class
        no longer holds a store or a context store of its own — it used to,
        and reached them here on every call, the way `friday/kernel/dag/prepare.py`
        argued for threading a string "through four layers" rather than a
        dict. That argument does not survive `FullContext`: the object *is*
        the point now, gathered in exactly one place
        (`friday.kernel.extraction.context.build_full_context`) so a fifth input
        cannot again mean a fifth signature to thread it through.
        """
        return build_input(context)

    async def run(
        self, context: FullContext, *, task_id: int | None = None, node: str | None = None
    ) -> tuple[Params | None, Clarify | None]:
        """Ask the model to fill the fields. `(None, None)` if the call failed.

        `task_id` and `node` name the call for the recording sink — they are
        not content the model sees, which is why they stay separate
        arguments rather than fields of `context`.

        The harness already swallows exceptions into `last_error`, so a
        `None` params here means "the model could not answer" — the workflow
        falls back to the structural check, which is the right behaviour:
        nothing to hallucinate means nothing to validate. Whether the model
        also asked about a field is independent of that — one extra
        turn covers the tool call landing before or after the field text.

        The turn budget is entirely the harness's now: one turn to answer,
        one that `run_structured` adds for the correction, and `tool_turns`
        for the skill tools where this extractor has any. Nothing is asked for
        here, which is what stops this line and the harness both adding one.
        """
        # No `extra_turns`: the one correction is `run_structured`'s own, and
        # the turns a skill fetch needs come from the harness, which is the
        # only thing that knows whether it wired any. This line used to add
        # one on top of both, which bought a second correction nobody decided
        # on.
        answered = await self._harness.run_structured(
            await self.would_ask(context),
            task_id=task_id,
            node=node,
        )
        if answered is None:
            if self._harness.refusal is not None:
                # Not "the model could not answer" — we did not ask it. The
                # difference decides what happens next: with no fields, the
                # structural check finds them missing and the reporter is
                # asked for the correlationId they wrote in their first
                # message. Nothing they say will change a ceiling, so this
                # goes to the operator instead.
                raise Refused(self._harness.refusal)
            # Either the model never answered, or it answered twice with
            # something that does not fit `params_cls` — and both are "no
            # extraction", which is what the caller acts on. They used to be
            # told apart here and were not worth telling apart: the hand-
            # written parser could not fail, so this branch only ever meant
            # the first, while the second arrived silently as a *successful*
            # extraction of nothing.
            log.warning("extractor %s produced nothing usable", self.name)
            # **Nothing, not "nothing plus a question".** The request for more
            # detail is part of the answer now, so an answer that did not
            # arrive carries no question either — where the capture this
            # replaced could survive a failed extraction and hand back a
            # half-run's worth of asking.
            return None, None
        filled, clarify = params_and_clarify(answered, self._params_cls)
        return _hygiene(filled), clarify


def build_extractor(
    *,
    params_cls: type[Params],
    harness: Harness,
    name: str,
) -> Extractor:
    """Wire a Harness to a Params class under a name.

    Use this when the extractor is built programmatically (e.g. from
    `config.yaml`). `register(...)` is the registration
    shortcut.

    The `task_type` for which this extractor is registered must match
    `params_cls`: registering an `api_issue` extractor with
    `params_cls=DocQuestionParams` would silently produce the wrong type at
    runtime. The check is enforced at registration, not at extraction, so a
    misconfigured system fails to start rather than producing a wrong answer.

    **No `context=`/`db=` any more** (ticket 15, D26): those reached the
    room, the domain memories and the open questions from inside this
    class; now `friday.kernel.extraction.context.build_full_context` gathers all
    three, called by node 0, which already holds both.

    **The harness must answer the shape this extractor claims.** Same
    argument as `register`'s check against `PARAMS`, one level down: a
    harness that declares a different `answers=` produces the wrong type
    at runtime, in the middle of a task, where the only symptom is fields
    that never fill in. Refusing here costs a restart.
    """
    if harness.answers is not answer_shape(params_cls):
        raise ValueError(
            f"the {name} extractor fills {params_cls.__name__}, but its "
            f"harness answers {harness.answers}"
        )
    return Extractor(harness=harness, params_cls=params_cls, name=name)


def registered() -> dict[str, Extractor]:
    """Snapshot of the registry, for inspection (e.g. by tests)."""
    return dict(_EXTRACTORS)


async def extract(
    task_type: str,
    context: FullContext,
    *,
    task_id: int | None = None,
    node: str | None = None,
) -> tuple[Params | None, Clarify | None]:
    """Run the extractor registered for `task_type` over `context`.

    Returns the filled Params, or None if no extractor is registered or the
    extractor failed. None is the right answer for "skip this step" — the
    caller treats it as "no extra information found, do not validate the
    hallucinated fields". The `Clarify`, if any, is independent of whether
    the params came back — the model may have called the tool and still
    written nothing usable, or the reverse.
    """
    ext = _EXTRACTORS.get(task_type)
    if ext is None:
        return None, None
    return await ext.run(context, task_id=task_id, node=node)





async def input_fingerprint(task_type: str, context: FullContext) -> str:
    """One string standing for everything this extractor is about to be shown.

    **The prompt itself, hashed** — not a reconstruction of it. Node 0 used to
    rebuild the two inputs it knew about (the field schema and the reporter's
    text) because a module outside this family may not reach into its prompt
    module. That was a fair compromise and it broke the day the prompt grew a
    third input: ticket 01 put the room's own facts in here, the reconstruction
    did not know about them, and so an operator who wrote down what
    `test.apero` is got a task that never read it — the fingerprint had not
    changed, so the mark replayed the stale answer with no model call. Which
    is the one scenario ticket 01 exists for, since the operator writes the
    fact *because* the task asked.

    Computing it here removes the class of bug rather than the instance:
    every input `would_ask` reads is a field of `context` now, so a new one
    cannot be forgotten — there is no second place to remember it in the
    first place (ticket 15, D26). `known` (ticket 08's D8, a fifth input
    that used to thread through five separate signatures) is exactly why
    this collapse mattered: a field getting filled shrinks the schema
    `would_ask` renders, and that is a real prompt change the digest has to
    see move, whichever field of `context` happens to carry it.

    Returns the empty string for an unregistered type, which is what `extract`
    answers for one too — a caller with no extractor has nothing to remember.
    """
    import hashlib

    ext = _EXTRACTORS.get(task_type)
    if ext is None:
        return ""
    said = await ext.would_ask(context)
    return hashlib.sha256(said.encode()).hexdigest()


def register(
    task_type: str,
    params_cls: type[Params],
    config: "AgentConfig",  # type: ignore[name-defined]  # noqa: F821
    *,
    #: The skill library, if this install has one. Handed to the harness,
    #: which wires the four tools and grants the turn they need — an
    #: extractor reading a skill that says where a correlationId lives is
    #: the case this is for.
    skills=None,
    record=None,
    spent=None,
) -> None:
    """Register one task type's extractor from configuration.

    One function for every task type rather than one per type: they differ
    only in which `Params` they fill, and three copies of the same twenty
    lines is three places for them to drift.

    The `params_cls` is the registry's own (`register_extractors` reads it from
    the same `TaskTypeSpec` the graph is built from), so the schema an extractor
    fills and the schema its type declares cannot drift apart.

    **No `context=`/`db=` any more** (ticket 15, D26): the room, the domain
    memories and the open questions were injected here and read per call
    from inside `Extractor`. They reach node 0's own `build_full_context`
    instead, from node 0's own dependencies — where the context store and
    the database already travel — so this registration only ever wires a
    model to a schema.
    """
    from friday.kernel.harness.harness import Harness, Refused

    from friday.kernel.harness.instruction_prompt import SkillMeta

    skills_meta = None
    if skills is not None and len(skills):
        skills_meta = [
            SkillMeta(
                name=s.name,
                description=s.description,
                mutability=s.mutability,
                location=str(skills.location_of(s.name)),
                allowed_tools=s.allowed_tools,
            )
            for s in skills.skills()
        ]
    _EXTRACTORS[task_type] = build_extractor(
        params_cls=params_cls,
        harness=Harness(
            config=config,
            instructions=build_instructions(
                skills_meta=skills_meta,
            ),
            skills=skills,
            # The shape this extractor answers, declared where it is built:
            # the type's own parameters *and* what it wants to ask about. The
            # harness generates the tool the answer arrives through and the
            # check it is validated by from this one class.
            answers=answer_shape(params_cls),
            record=record,
            spent=spent,
        ),
        name=f"{task_type}_extractor",
    )


def _hygiene(params: Params) -> Params:
    """Trim every value, and treat absent-looking text as absent.

    Moved here from triage when triage stopped producing values. The live
    provider taught us the lesson it protects against: the model writes the
    *string* `"null"` often enough that an unnormalised value is mistaken for a
    real one — and a workflow that believes it has a correlationId will never
    ask for the one it needs.
    """
    from friday.kernel.text.param_hygiene import clean

    return type(params)(
        **{
            f.name: clean(value) if isinstance(value := getattr(params, f.name), str)
            else value
            for f in fields(params)
        }
    )


#: `_parse`, `_json_object`, `_undecorate` and `_DECORATION` lived here and
#: are gone. They turned whatever a model said into a kwargs dict by guessing
#: — a brace scan, then a `key: value` line scraper that read a Markdown
#: bullet as a field name — and the guess could not fail: unreadable output
#: became `{}`, every field of a `Params` has a default, and an empty
#: extraction arrived at the caller looking exactly like a successful one.
#: Nothing checked the *types* either, so `environment: ["a","b"]` built a
#: `Params` without complaint and surfaced later as `unhashable type: 'list'`
#: from inside `validate`, as a hand-over quoting a Python error at the
#: operator. `Harness.run_structured` replaces all of it: the shape is
#: described to the model from the dataclass itself, the answer is validated
#: against that dataclass in this process, and an answer that does not fit
#: earns one correction turn instead of a guess. Finding JSON inside a
#: fenced, `<think>`-prefixed reply is still needed and still done — that
#: part was never guesswork, it is what the provider actually returns — and
#: now lives in `friday/kernel/harness/structured.py` where the summariser can reach
#: it too.


def register_extractors(
    config: Config,
    *,
    skills=None,
    record=None,
    spent=None,
) -> None:
    """Wire every extractor the configuration declares.

    Composition root calls this once at startup and learns nothing about any
    individual extractor. Adding one is registering a task type (ticket 11) and a
    block in `config.yaml`, not a change there — the types come from the registry
    the graphs are built from, so an extractor cannot be wired to a type with no
    graph or missed for one that has.

    A missing block is a warning rather than a failure, because a broken
    install that starts and says what is wrong beats one that will not start.
    It is a loud warning: nothing else fills those fields.

    **One block for every type, not one per type.** There were three —
    `extractor_api_issue`, `extractor_access_request`, `extractor_doc_question`
    — on the argument that the jobs differ enough to want different models:
    reading a correlationId out of a stack trace is not reading a repo name
    out of a request. That argument was never wrong, it was just never
    *taken*: all three blocks held identical values for as long as they
    existed, so what it actually bought was one configuration written three
    times and three places to edit when the provider changes. Splitting the
    key back out is a small change on the day a type genuinely needs its own
    model; reserving it in advance cost more than it saved.

    Each type still gets its own *agent* — its own prompt, its own `Params`
    schema, its own registration — because that part was never the
    duplication. Only where the model lives is shared.
    """
    from friday.kernel.dag import registry

    agent_config = config.agents.get("extractor")
    if agent_config is None:
        log.warning(
            "no 'extractor' agent in config.yaml — every task type will open "
            "with no parameters and the reporter will be asked for what they "
            "already said"
        )
        return

    for task_type, params_cls in registry.decision_params().items():
        register(
            task_type,
            params_cls,
            agent_config,
            skills=skills,
            record=record,
            spent=spent,
        )
