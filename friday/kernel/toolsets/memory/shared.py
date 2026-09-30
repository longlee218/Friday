"""What every memory tool needs beside its own body: the run's scope, the
per-memory character cap, and the one answer for an id that does not
resolve.

Split out of the single `memory.py` module into a package (one file per
tool) so descriptions could be declared explicitly instead of read off a
docstring — the rule ticket 23 introduced for `core.repos`, applied here.
"""

from __future__ import annotations

from friday.kernel.domain.state import FridayState

__all__ = ["TEXT_CHARS", "NotWired"]

#: A memory is a sentence, not a document. Longer than this is a summary that
#: belongs in the channel's context file.
TEXT_CHARS = 500


class NotWired(RuntimeError):
    """The agent holding these tools was run without a `FridayState`.

    Its own class so the log line names the mistake. Without it the state was
    dereferenced straight off `ctx.deps`, an `AttributeError` on `None`
    reached the run's tool-failure hook, and the operator was told a tool was
    unavailable — which reads as the store being down, and is instead an agent
    that was built without `context_type=FridayState` or run without a
    `context=`. That is the failure mode of wiring a *new* agent to these,
    which is the next thing that happens to this file.
    """


def state_of(ctx) -> FridayState:
    """The run's state, or a failure that says what is actually wrong.

    Every tool here reads it through this, and hands it to the store whole —
    the store takes the state *as* the scope, reading the room, the task, the
    agent and the source message off it. The model still gets "unavailable"
    either way, which is true — without a state there is no room, and so no
    memory to reach — but the operator gets a sentence they can act on.
    """
    state = getattr(ctx, "deps", None)
    if not isinstance(state, FridayState):
        raise NotWired(
            "memory tools were run without a FridayState: build the agent with "
            f"context_type=FridayState and pass context= to run() (got {state!r})"
        )
    return state


def bounded(text: str, chars: int = TEXT_CHARS) -> str:
    """Cut to the cap rather than refusing.

    A refusal here would be a turn spent on nothing: the agent would have to
    be told what the cap is and asked to write the line again, and it has
    already said what it meant. A truncated memory is a worse memory, not a
    failed step.
    """
    text = text.strip()
    return text if len(text) <= chars else text[:chars].rstrip()


def no_such(memory_id: str) -> str:
    """The one answer for an id that is gone, that never existed, and that
    belongs to another room. Distinguishing them would tell an agent something
    about a scope it cannot read."""
    return (
        f"no memory {memory_id!r} here — search again, ids are exactly as "
        f"memory_search returns them"
    )
