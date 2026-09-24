"""What node 0 is shown about a task, gathered in one place.

Board `what-the-room-already-knows`, ticket 15, D26: the full build's own
gather function, beside the extraction family's prompt module, following
the convention `friday/triage/context.py` settled for the light build in
ticket 14 — one module, one frozen value, content rather than rendered
prompt text (D4).

**This module reads. It never writes, and it must never import a section
builder, construct a `Section`, or join anything** — the same rule ticket
14 held its own gather module to. `tests/test_context_builders.py`'s `ast`
guard covers both modules from one list.

Before this ticket, the full build was split across two places that did
not know about each other: node 0 fetched the transcript — with its budget,
its cooldown and its ineffective-compaction bookkeeping (ticket 08) — and
the extractor's own `would_ask` fetched the room, the domain memories and
the open questions. Ticket 08's `known` had to thread through five
signatures to cross from the first place to the second. One gather ends
that: `build_full_context` does every read, and one `FullContext`
travels the whole path from node 0 to `build_input`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, fields

from friday.kernel.domain.models import Memory, Params
from friday.store.db import estimated_tokens

__all__ = ["FullContext", "build_full_context"]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FullContext:
    """Everything node 0 is shown about one task: the reporter's transcript,
    the domain-kind rows written about its room, the questions this task has
    already asked and not had answered, and the parameters it already has.

    There was a `room` field, the channel's YAML context file. The files are
    gone (board `read-it-the-way-the-operator-does`, ticket 10): what the
    operator wrote about a room is rows with `origin=admin`, here or for
    every room, and they arrive in `domain_memories` beside the ones a model
    wrote. The summary does not reach the extractor at all — `readers_for`
    gives it to triage and the responder.

    Named `transcript` rather than `text` so it cannot be mistaken for one
    message's body — it is `Database.original_text_for`'s output, already
    under whatever budget applies, or `None` for a task with nothing linked
    to it yet.

    `transcript_over_budget` is the one field beyond the five the ticket
    names, and it earns its place: D6's cooldown is a *write* (node 0's own
    `record_ineffective_compaction`), and this builder never writes. The
    field is how node 0 learns whether to, without the builder doing it
    itself — `False` whenever no budget applies, whether because none is
    configured or because this task is already on cooldown.
    """

    transcript: str | None
    domain_memories: Sequence[Memory]
    asked: Sequence[str]
    known: Params
    transcript_over_budget: bool = False


async def build_full_context(
    db,
    *,
    channel_id: str | None,
    task_id: int | None,
    known: Params,
    budget_tokens: int | None = None,
) -> FullContext:
    """The one place node 0 gathers what an extraction needs.

    **Reads only.** The cooldown check (`db.compaction_on_cooldown`) is a
    read; so is everything else here. A task already on cooldown is read
    with no budget at all — the same as an install with none configured —
    because truncation cannot help a transcript that has already proven too
    large for it twice, and this function must not spend a check on that
    again (D6). Recording that a *new* pass is still over budget is node 0's
    own write, decided from `transcript_over_budget` after this returns.

    `task_id` and `channel_id` are both allowed to be `None` — a bare call
    with nothing to resolve, the same convenience `friday.triage.context
    .build_light_context` gives a caller with no store. `db` may itself be
    `None` for the same reason.
    """
    on_cooldown = (
        db is not None
        and budget_tokens is not None
        and task_id is not None
        and await db.compaction_on_cooldown(task_id)
    )
    effective_budget = None if on_cooldown else budget_tokens
    transcript = (
        await db.original_text_for(task_id, budget_tokens=effective_budget)
        if db is not None and task_id is not None
        else None
    )
    over_budget = (
        effective_budget is not None
        and transcript is not None
        and estimated_tokens(transcript) > effective_budget
    )
    asked = (
        await db.unanswered_questions(task_id)
        if db is not None and task_id is not None
        else ()
    )
    domain_memories = (
        await db.domain_memories(channel_id)
        if db is not None and channel_id is not None
        else ()
    )
    log.debug(
        "full context for task %s: transcript %d chars (~%d tokens, over "
        "budget: %s), %d domain memor%s, %d open question%s, "
        "known fields: %s",
        task_id,
        len(transcript or ""),
        estimated_tokens(transcript or ""),
        over_budget,
        len(domain_memories),
        "y" if len(domain_memories) == 1 else "ies",
        len(asked),
        "" if len(asked) == 1 else "s",
        sorted(f.name for f in fields(known) if getattr(known, f.name, None)),
    )
    return FullContext(
        transcript=transcript,
        domain_memories=domain_memories,
        asked=asked,
        known=known,
        transcript_over_budget=over_budget,
    )
