"""What an extractor's prompt looks like, and from what it is assembled.

Assembled from the shared section builders, like every other agent. One job
text serves every extractor because they differ only in which fields they
fill; the fields themselves are per call, read off the params class.

**A voice would actively hurt here.** An extractor told to write in Vietnamese
puts `sản xuất` where `friday/sdk/validation.py` wants `production`, the
value fails its rule, and the reporter is asked to confirm what they already
said. So there is no `soul` in this prompt and there should not be one.

**It can ask, so it is told how — but asking here does not stop the work.**
`ask_about` is a field of the answer itself, holding a closed set of this
type's own field names, which is why the clarification section is rendered here
and not for triage: the door is actually in the room. It is rendered
`blocking=False`, and that flag is the whole difference between this prompt
working and this prompt silently breaking: told to wait for an answer before
proceeding, the model can ask and return no fields at all — and the parser of
the day read that as
`{}`, every field of a `Params` has a default, and an empty extraction came
back as a *successful* one, so the reporter was asked for everything they
just wrote. `Harness.run_structured` means an unreadable answer is `None`
rather than an empty success now; this flag is still what stops the model
throwing away fields it had already read.
"""

from __future__ import annotations

from collections.abc import Sequence

from friday.kernel.extraction.context import FullContext
from friday.kernel.harness.instruction_prompt import (
    SkillMeta,
    assemble,
    clarification_system,
    critical_reminder,
    facts,
    job,
    memory,
    outstanding_questions,
    role,
    room_facts,
    said,
    skill_system,
    thinking_style,
    trust_boundary,
)
from friday.kernel.harness.structured import describe

__all__ = ["build_input", "build_instructions"]

#: The job. "Reply in JSON only, with the schema fields as keys" is a contract
#: with `Harness.run_structured`, which checks the answer against the params
#: dataclass and asks again once if it does not fit — reworded freely, but the
#: JSON promise stays.
JOB = """You fill structured fields from what someone wrote.

You are shown the field schema (`<fields>`) — the names and what each one is
for — and everything the reporter has said about this (`<transcript>`), oldest
first. The answer to a
question they were asked is in there as an ordinary later message, so read all
of it, not only the first line.

You are the only thing that reads this message for what it contains. Nothing
produced these fields before you and nothing corrects them after, except a
check that a value you did supply has the right shape.

Asking about a field and filling one are separate: do both when both apply,
and reply in JSON for whatever you did find."""

THINKING = [
    "Read everything they said before filling anything in.",
    "For each field, find the value they actually wrote — not one you can infer.",
    "If a field is not there, it is null. Absent is an answer.",
]

#: These three have each cost something. The first: a paraphrased
#: correlationId sends somebody through the wrong request. The second is a
#: contract with this package's own parser. The third is what `null` buys.
REMINDERS = [
    "Copy matching values verbatim — never paraphrase a field that asks for a "
    "literal value.",
    "Reply in JSON only, with the schema fields as keys.",
    "A wrong value costs somebody an afternoon; a null one costs a question.",
]

#: Kept as an attribute because tests pin sentences in it.
INSTRUCTIONS = JOB


def build_instructions(
    skills_meta: Sequence[SkillMeta] | None = None,
) -> str:
    """Who it is, the job, how to read, how to ask, what not to get wrong."""
    return assemble(
        role(
            "Friday", "a field extractor", "you lift values out of what someone wrote"
        ),
        trust_boundary(),
        job(JOB),
        thinking_style(THINKING),
        clarification_system(
            "naming those fields in `ask_about`, and why in `because`",
            blocking=False,
        ),
        # Skills reach every agent now (the operator's call, 2026-09-07).
        # For an extractor the case is direct: a skill saying where a
        # correlationId lives is the difference between lifting one out of a
        # stack trace and asking the reporter for what they already sent.
        skill_system(skills_meta),
        critical_reminder(REMINDERS),
    )


def build_input(context: FullContext) -> str:
    """The field schema, what the room is known to be, then the reporter's
    words — in that order, and the order is the cache.

    **One value, not five arguments** (board `what-the-room-already-knows`,
    ticket 15, D26): `context` is gathered by `friday.kernel.extraction.context
    .build_full_context`, the only place node 0's own build resolves a
    room, reads the domain memories, reads what has been asked, or reads
    the transcript under its budget. This function renders; it does not
    gather.

    Each field's meaning is its `doc` metadata on the params class — the
    field and its meaning live on the same line there, so they cannot drift
    apart. This renders them; it does not define them. The class itself is
    `type(context.known)` — `known` is always a real `Params` instance
    (never `None`; a task with nothing filled in yet is `params_cls()`,
    every field its own default), so there is no params class to pass
    separately any more.

    **`context.known` drops an already-filled field from the schema**
    (ticket 08, D8: "a field the parameter schema names is compacted into
    the task's parameters, because the extractor has already copied it
    verbatim and the store already persists it"). `_fill` already refuses
    to let a later pass *overwrite* a filled field; this is the other half
    — not asking about it again, so a task with three of four fields
    answered pays for one line of schema and not four, on every pass a busy
    room causes.

    **The room goes between them, not first.** Stable-first, and which is
    stabler is not a judgement call: one `Harness` per task type serves every
    conversation, so the field schema is byte-identical across every call this
    agent makes, and the room is not. Putting the room first would break the
    shared prefix for every conversation but one.

    **The room goes in the input, never in the instructions** (ticket 01's
    D21). Two reasons pointing the same way. Instructions are built once per
    type and shared by every conversation, so a room's facts could not live
    there without a `Harness` per room. And a room's facts are derived from
    what people wrote, so they belong on the channel a model reads as somebody
    speaking rather than the one it reads as its own operator — text that
    arrives in one call's input cannot rewrite the prompt of every later call.

    **`context.asked` is what this task has already asked the reporter and
    not had answered** — derived from the outbox, with no model involved, so
    it cannot be wrong in an interesting way. It goes in `memory`'s
    conversation slot because that is what the slot is: this exchange, where
    the room's facts are the channel. The failure it exists for is recorded
    — the system asked for an environment, nobody answered, and the next
    pass was free to ask again because nothing in the prompt said a question
    was outstanding.

    A room with nothing written about it renders no section at all, so the
    prompt of an unconfigured install is byte-identical to what it was before
    this existed. A test says so, because "close enough" would still cost
    every extractor in every such install its prefix.

    **`context.domain_memories` is the channel slot, whoever wrote it**
    (board `what-the-room-already-knows`, ticket 10, D14; board
    `read-it-the-way-the-operator-does`, ticket 10). It answers one question
    — what is this room known to be — from two producers: the operator's
    hand, `origin=admin` rows for this room and for every room, which were a
    YAML file's `base` and `overrides`; and an agent's own domain-kind
    memories. `room_facts` renders both, labelled by origin, in one block
    rather than two slots: an agent given two blocks for the same kind of
    thing would have to work out that they mean one another, the same
    reasoning `memory`'s own docstring gives for not splitting
    `conversation` and `channel` further.

    The reporter's own words go through the one boundary. They used to be
    interpolated raw: a message carrying `</task><critical_reminder>…` put its
    own section into this prompt, and the extractor is the agent most worth
    aiming that at — it is the one that decides what a task knows.
    """
    # Generated from the params dataclass, by the same function that
    # generates the summariser's — so the shape the model is told is the
    # shape `Harness.run_structured` checks its answer against. This built
    # its own `- name: doc` lines until review found what that cost: the
    # description named no *types*, while the validation refuses a wrong one,
    # so the highest-volume structured path was refusing answers it had never
    # told the model how to avoid.
    #
    # `omit=context.known` is ticket 08's D8 — a field already in the task's
    # parameters drops out of the schema, on the truthy test `_fill` uses.
    #
    # **The *tool's* parameters do not shrink with it**, and that is a choice
    # rather than an oversight (board `every-answer-has-a-shape`, ticket 08).
    # The answer shape is generated once per task type and cached, because
    # `build_extractor` and the run's own terminator both compare classes; one
    # per combination of already-known fields would be a class per call, and
    # a tool schema that changed shape between calls is the opposite of what
    # the stable-prefix work in this file was for. What the shrinking prompt
    # buys is still bought: it is what the model is *told* to fill, and it is
    # what `input_fingerprint` hashes, so a field getting filled still moves
    # the mark and still earns a fresh extraction.
    schema = describe(type(context.known), omit=context.known) or "(no fields)"
    channel_body = room_facts(list(context.domain_memories))
    return assemble(
        facts("fields", schema),
        memory(
            conversation_body=outstanding_questions(context.asked),
            channel_body=channel_body,
        ),
        said("transcript", context.transcript or ""),
    )
