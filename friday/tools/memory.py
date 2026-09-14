"""What an agent chooses to remember, and can go back and correct.

Wired to the responder (ticket 09's D9) — the obvious first, since it is the
agent that writes text a person reads and so the one whose room-specific
habits are worth remembering. Triage is the obvious never: it stops on its
first tool call by design, so a memory tool there would end the run before it
classified, the same reason it has no skill tools either.

This replaces `remember` rather than restoring it. `remember` wrote to a
staging tier that nothing read back, and a promotion pass moved only what two
approved tasks corroborated. That restraint bought a real thing — an agent
reading its own unreviewed notes drifts with no floor — but it cost the two
operations that make memory usable: an agent that cannot see what it wrote
cannot correct it, and `memory_update`/`memory_delete` have nothing to name.
**Reversing it is a design change**, recorded in `docs/DESIGN.md` rather than
implied by this module existing.

What the staging tier was protecting is kept, by three properties instead:

**A memory reaches a model only as a tool result, never as instructions.**
`remember`'s successor tier did the opposite — `Harness` used to append the
promoted block to `instructions` (that mechanism, `_with_notes`, is deleted
along with the tier itself, ticket 09's D9), and commit f0686f2 is the day a
promoted note closed its own section and could rewrite the instructions of
every call that agent made afterwards. Text that only ever arrives as tool
output cannot do that, whatever it says. It also means memory costs nothing
on a run that does not search: a prompt-cache argument and a safety argument
pointing the
same way.

**Scope is attached by the runtime, never named by the model.** The same rule
`remember` had for `task_id`: the parameters a model supplies are the
parameters it can get wrong, and "which room is this" is not a question worth
letting it answer. An agent working in one channel cannot read or write
another's.

**An id it did not read back is an id that does not resolve.** Ids are opaque
and sparse rather than sequential, so a hallucinated one fails instead of
landing on somebody else's row. With `1, 2, 3…` a model that invents `m12`
deletes whatever `m12` happens to be.

The docstrings below *are* the schema: `harness.tool` leaves
`use_docstring_info` at the SDK's default of True — the knob LangChain calls
`parse_docstring`, where it defaults to False — so an
`Args:` line reaches the model as that parameter's description. The one for
`memory_id` is load-bearing: without it the model is handed a bare
`memory_id: string` and nothing saying it must be one it read back, which is
the whole of what makes an opaque id safe.
"""

from __future__ import annotations

import logging

from friday.agent.harness import ToolContext, tool
from friday.agent.instruction_prompt import memory_lines
from friday.domain.memory_guard import InstructionShaped
from friday.domain.models import CandidateStatus, MemoryKind, FridayState

__all__ = ["NotWired", "RESULTS", "TEXT_CHARS", "FridayState", "memory_tools"]

log = logging.getLogger(__name__)


#: A memory is a sentence, not a document. Longer than this is a summary that
#: belongs in the channel's context file.
TEXT_CHARS = 500

#: What one search returns. Enough to choose from, few enough that the agent
#: still has to have written something worth finding.
RESULTS = 8

# These were a `Limits` dataclass with a third field, `per_scope`, and a
# `limits=` parameter on the factory — a docstring claiming the numbers could
# not drift, over an arrangement in which they could only drift:
#
#   * the tool docstrings *are* the schema and cannot be f-strings, so they
#     said "eight" and "500" while the parameter existed precisely so a caller
#     could pass three and two hundred;
#   * `per_scope` had no reader in this module and no place in the store
#     contract below, so a store enforcing it would have had to keep its own
#     copy of the number — exactly the drift the class said it prevented.
#
# Two constants and no parameter, with the prose built from them below. The cap
# on how many memories one scope may hold is the store's, stated in ticket 09,
# because the store is the only thing that can enforce it.


def memory_tools(db):
    """The five tools, bound to one store. The run's state arrives per run.

    A factory for the same reason `search_skills_tool` is one: what an agent
    can reach is composition, not something the agent declares. Returns them
    in a list to be handed to `Harness(tools=...)`; an agent gets all five or
    none, because four of them are unusable without the fifth (`search`).

    The split is by lifetime. `db` lives as long as the process, so it is
    closed over; the state lives as long as one run, so it is
    `Harness.run(context=FridayState(...))` and each tool reads it off
    `ctx.context` through `_state`. An agent given these must therefore be
    built with `context_type=FridayState`.

    A store that raises is not this module's problem to phrase. `harness.tool`
    replaces the SDK's failure message for every tool here, because the
    default leaks `str(error)` to the model and tells it to try again — and
    "try again" after a write that may have landed is how a room ends up with
    the same memory twice.

    `db` is `friday/store/db.py`'s `Database`, answering five methods:

        memory_search(scope, query, *, limit)  -> list[Memory]
        memory_add(scope, text)                -> Memory | None
        memory_update(scope, memory_id, text)  -> Memory | None
        memory_delete(scope, memory_id)        -> bool
        propose_memory(scope, text, *, kind)   -> MemoryCandidate

    `None`/`False` means two different things depending on which method gives
    it, and both are deliberate rather than an overloaded shorthand. From
    `memory_update`/`memory_delete`, it means "no memory with that id **in
    this scope**" — the same answer whether the id never existed, belongs to
    another channel, or was already deleted, so an agent cannot learn a row
    exists in a scope it cannot read. From `memory_add`, it means the channel
    is at `Database.MEMORY_PER_CHANNEL` — nothing is evicted to make room, so
    the tool tells the model to correct or remove something on purpose
    instead.
    """

    async def memory_search(ctx: ToolContext[FridayState], query: str) -> str:
        """Find what is already known about something, before assuming nothing is.

        Search first. What you are about to work out may have been worked out
        already, by an earlier run that wrote it down for exactly this moment.

        Returns up to {RESULTS} lines, newest first — not scored or ranked,
        only ordered by when each one was written, so a precise memory older
        than {RESULTS} vaguer ones on the same words will not be in the list.
        Narrow the query rather than trust the order. Each line is
        `id: text` — the id is what memory_update and memory_delete take, so
        keep it if you intend to correct or remove that line. Nothing found
        comes back as a plain sentence saying so, which is an answer, not an
        error.

        Only this channel's memory is searched. There is no way to reach
        another room's, and nothing you write here will be visible there.

        Args:
            query: a phrase describing what you want to know, in the words you
                would use to describe it — not an id, and not a question.
        """
        found = await db.memory_search(
            _state(ctx), query, kind=MemoryKind.VOICE, limit=RESULTS
        )
        log.info("memory searched: %r -> %d", query, len(found))
        return memory_lines(found)

    async def memory_add(ctx: ToolContext[FridayState], text: str) -> str:
        """Write down something a later run would otherwise have to work out again.

        Worth writing: how a system actually behaves once you have established
        it, a person's standing preference, a step that turned out to be
        necessary. Not worth writing outright: anything readable off the task
        you are working on. Something you believe but are not fully confident
        of is `memory_propose`'s job, not this one's — this is read back as
        fact, not as a guess, so write here only what you would stand behind
        in a month.

        Search before you write. A second copy of something already known is
        worse than nothing: it takes a slot, and the two will disagree the day
        one of them is corrected.

        Returns the new id, so you can correct it later in this same run.

        Args:
            text: one sentence, at most {TEXT_CHARS} characters, that will
                make sense to a run that has none of your current context.
        """
        state = _state(ctx)
        kept = _bounded(text)
        try:
            written = await db.memory_add(state, kept, kind=MemoryKind.VOICE)
        except InstructionShaped as refused:
            log.info("memory refused for %s: %s", state.agent, refused)
            return str(refused)
        if written is None:
            # The store's own cap, not a failure — see `Database.MEMORY_PER_CHANNEL`.
            # Nothing is evicted to make room, so the model has to make room
            # itself: correct something with memory_update, or remove
            # something wrong with memory_delete.
            return (
                "this channel's memory is full — use memory_update to correct "
                "something already here, or memory_delete to remove something "
                "that turned out to be wrong, then try again"
            )
        log.info("memory added by %s: %r", state.agent, kept)
        return f"remembered as {written.id}"

    async def memory_propose(ctx: ToolContext[FridayState], text: str) -> str:
        """Suggest something worth remembering, without writing it yet.

        Use this instead of memory_add when you believe something but are not
        fully confident of it, or when being wrong about it would cost more
        than an awkward sentence — a claim a later task might act on. The
        operator reviews it before anything reads it back; nothing changes
        for this run or any other in the meantime, and there is nothing to
        poll or wait for.

        Board `what-the-room-already-knows`, ticket 12 (D19, D20): this
        restores the review floor the old `remember()` tool had, without
        restoring the tier it staged into — that one had a producer and
        nobody looking; this has both.

        Returns the candidate's id, for your own record.

        Args:
            text: one sentence, at most {TEXT_CHARS} characters, that will
                make sense to a run that has none of your current context.
        """
        state = _state(ctx)
        kept = _bounded(text)
        proposed = await db.propose_memory(state, kept, kind=MemoryKind.VOICE)
        log.info(
            "memory proposed by %s: %r (%s)", state.agent, kept, proposed.status
        )
        if proposed.status != CandidateStatus.PENDING:
            # `propose_memory` resolves immediately when the message it is
            # scoped to already carries a verdict — the operator marked the
            # classification before this run got here. Reporting the actual
            # outcome is more useful than telling the model to wait for a
            # mark that has already happened.
            outcome = "accepted" if proposed.status == CandidateStatus.ACCEPTED else "rejected"
            return f"proposed as {proposed.id} — already marked {outcome}"
        return f"proposed as {proposed.id} — waiting for a mark"

    @tool
    async def memory_update(
        ctx: ToolContext[FridayState], memory_id: str, text: str
    ) -> str:
        """Correct something already written down, in place.

        Use this rather than adding a second line when what is stored is now
        wrong or incomplete — two lines that contradict each other are worse
        than either alone, and nothing later can tell which one won.

        The previous text is replaced, not kept. Correct what is wrong; do not
        rewrite a line that is merely phrased differently from how you would
        phrase it.

        Args:
            memory_id: the id exactly as memory_search returned it.
            text: what it should say instead, in full — this replaces the line
                rather than being appended to it.
        """
        state = _state(ctx)
        try:
            updated = await db.memory_update(state, memory_id, _bounded(text))
        except InstructionShaped as refused:
            log.info("memory update refused for %s: %s", state.agent, refused)
            return str(refused)
        if updated is None:
            return _no_such(memory_id)
        log.info("memory %s updated by %s", memory_id, state.agent)
        return f"{memory_id} updated"

    @tool
    async def memory_delete(ctx: ToolContext[FridayState], memory_id: str) -> str:
        """Remove something that turned out to be wrong.

        For a line that is *false*, not one that is old — a fact you have just
        disproved, a preference the person has told you they no longer have.
        Something merely inaccurate is a memory_update.

        Deleting is visible to the operator afterwards, along with what the
        line said. Delete what is wrong; you do not have to tidy.

        Args:
            memory_id: the id exactly as memory_search returned it.
        """
        state = _state(ctx)
        if not await db.memory_delete(state, memory_id):
            return _no_such(memory_id)
        log.info("memory %s deleted by %s", memory_id, state.agent)
        return f"{memory_id} forgotten"

    # The numbers the prose quotes are the numbers the code enforces, because
    # they are the same object. A docstring cannot be an f-string and these two
    # are the schema the model reads, so they are written in here — the shape
    # `classify` already uses, for the same reason.
    # `replace` rather than `format`: a docstring is prose and may hold a brace.
    memory_search.__doc__ = memory_search.__doc__.replace("{RESULTS}", str(RESULTS))
    memory_add.__doc__ = memory_add.__doc__.replace("{TEXT_CHARS}", str(TEXT_CHARS))
    memory_propose.__doc__ = memory_propose.__doc__.replace(
        "{TEXT_CHARS}", str(TEXT_CHARS)
    )

    return [
        tool(memory_search),
        tool(memory_add),
        tool(memory_propose),
        memory_update,
        memory_delete,
    ]


class NotWired(RuntimeError):
    """The agent holding these tools was run without a `FridayState`.

    Its own class so the log line names the mistake. Without it the state was
    dereferenced straight off `ctx.context`, an `AttributeError` on `None`
    reached `harness._tool_failed`, and the operator was told a tool was
    unavailable — which reads as the store being down, and is instead an agent
    that was built without `context_type=FridayState` or run without a
    `context=`. That is the failure mode of wiring a *new* agent to these,
    which is the next thing that happens to this file.
    """


def _state(ctx) -> FridayState:
    """The run's state, or a failure that says what is actually wrong.

    Every tool here reads it through this, and hands it to the store whole —
    the store takes the state *as* the scope, reading the room, the task, the
    agent and the source message off it. The model still gets "unavailable"
    either way, which is true — without a state there is no room, and so no
    memory to reach — but the operator gets a sentence they can act on.

    Named for what it returns rather than for what this module does with it.
    It was `_scope`, which was true while the only thing in the object was a
    memory's scope and stopped being true the moment the run's own state took
    that slot (D10) — and the rename was left half-done for a commit, so this
    function said "state" in its name and "scope" in every line of its body.
    """
    state = getattr(ctx, "context", None)
    if not isinstance(state, FridayState):
        raise NotWired(
            "memory tools were run without a FridayState: build the agent with "
            f"context_type=FridayState and pass context= to run() (got {state!r})"
        )
    return state


def _bounded(text: str, chars: int = TEXT_CHARS) -> str:
    """Cut to the cap rather than refusing.

    A refusal here would be a turn spent on nothing: the agent would have to
    be told what the cap is and asked to write the line again, and it has
    already said what it meant. A truncated memory is a worse memory, not a
    failed step.
    """
    text = text.strip()
    return text if len(text) <= chars else text[:chars].rstrip()


def _no_such(memory_id: str) -> str:
    """The one answer for an id that is gone, that never existed, and that
    belongs to another room. Distinguishing them would tell an agent something
    about a scope it cannot read."""
    return (
        f"no memory {memory_id!r} here — search again, ids are exactly as "
        f"memory_search returns them"
    )
