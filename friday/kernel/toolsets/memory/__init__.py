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

**Split into a package, one file per tool** (`search.py`, `add.py`,
`propose.py`, `update.py`, `delete.py`, shared state/bound/no-such helpers in
`shared.py`), each declaring its `description=` and parameter `Field`s
explicitly rather than reading them off a docstring — the rule ticket 23
introduced for `core.repos`, applied here to close the last docstring-as-schema
tool in the core (`memory_search.__doc__.replace("{RESULTS}", ...)` and the
`Args:` blocks this module used to carry). A docstring on each tool function
now stays only as a note for a human reader.
"""

from __future__ import annotations

from friday.kernel.toolsets.memory.add import build_add
from friday.kernel.toolsets.memory.delete import build_delete
from friday.kernel.toolsets.memory.propose import build_propose
from friday.kernel.toolsets.memory.search import RESULTS, build_search
from friday.kernel.toolsets.memory.shared import TEXT_CHARS, NotWired
from friday.kernel.toolsets.memory.update import build_update

__all__ = [
    "MEMORY_READS",
    "MEMORY_WRITES",
    "RESULTS",
    "TEXT_CHARS",
    "NotWired",
    "memory_tools",
]

#: The two toolsets these five are granted as (build-the-spine ticket 22): the
#: toolset is the unit of a grant, so an agent that reads what nobody vouches
#: for can hold the reads without the writes. `core.memory` reads and proposes
#: — a proposal waits for the operator before anything reads it back;
#: `core.memory_write` writes what is read back as fact, and can rewrite or
#: remove any row in the room. The responder builds all five itself.
MEMORY_READS = ("memory_search", "memory_propose")
MEMORY_WRITES = ("memory_add", "memory_update", "memory_delete")


def memory_tools(db):
    """The five tools, bound to one store. The run's state arrives per run.

    A factory for the same reason `search_skills_tool` is one: what an agent
    can reach is composition, not something the agent declares. Returns them
    in a list to be handed to `Harness(tools=...)`. The responder gets all
    five; a spine agent gets them as two toolsets (`MEMORY_READS`,
    `MEMORY_WRITES`), and `core.memory_write` needs `core.memory` beside it,
    since `memory_update`/`memory_delete` take ids only `memory_search` gives.

    The split is by lifetime. `db` lives as long as the process, so it is
    closed over; the state lives as long as one run, so it is
    `Harness.run(context=FridayState(...))` and each tool reads it off
    `ctx.deps` through `shared.state_of`. An agent given these must therefore
    be built with `context_type=FridayState`.

    A store that raises is not this module's problem to phrase. The run's
    tool-failure hook replaces the model-facing message for every tool here with
    "unavailable, carry on", because leaking `str(error)` and telling the model
    to try again — after a write that may have landed — is how a room ends up
    with the same memory twice.

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
    return [
        build_search(db),
        build_add(db),
        build_propose(db),
        build_update(db),
        build_delete(db),
    ]
