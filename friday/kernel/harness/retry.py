"""Which provider failures are worth another attempt, and the bookkeeping of
the attempts `Harness._attempts` makes: what a run was about, which attempt is
in flight, and the reason a person reads when it gave up.

No vendor import: the agent SDK stays in `harness.py` alone.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

from friday.kernel.domain.state import FridayState


@dataclass(frozen=True, slots=True)
class _About:
    """What a run was about, for the rows it produces. A value rather than three
    parameters threaded through `_settle`, because they travel together."""

    message_id: str | None = None
    task_id: int | None = None
    node: str | None = None

    @classmethod
    def of(
        cls,
        context: Any,
        *,
        message_id: str | None = None,
        task_id: int | None = None,
        node: str | None = None,
    ) -> "_About":
        """What this call was about, read off the run's state where there is one
        (D8) and named explicitly where there is not. An explicit argument still
        wins, for a caller that knows better than the state it was handed.
        `node` is never on the state — it is which step of a graph asked."""
        if isinstance(context, FridayState):
            message_id = message_id or context.message_id
            task_id = task_id if task_id is not None else context.task_id
        return cls(message_id=message_id, task_id=task_id, node=node)

    def stamp(self, call):
        """The call with what the caller knew about it, and nothing else
        overwritten: `latency_ms` was measured by the hook and is not ours."""
        if not (self.message_id or self.task_id or self.node):
            return call
        return replace(
            call,
            message_id=self.message_id,
            task_id=self.task_id,
            node=self.node,
        )


#: What is worth calling again. A list rather than a guess from the message: a
#: 400 is the provider saying the request itself is wrong, and paying to ask it
#: a second time buys nothing.
_TRANSIENT = (
    APIConnectionError, APITimeoutError, RateLimitError, InternalServerError,
    # An attempt past its bound (`_attempts`).
    TimeoutError,
)

#: Statuses the SDK gives no class of its own, and that are still worth another
#: call. Only 408 today.
_RETRY_STATUSES = frozenset({408})


def _transient(exc: Exception) -> bool:
    # The model exhausting its correction budget, or the run hitting its turn
    # cap (`UnexpectedModelBehavior`, `UsageLimitExceeded`), is not retried
    # either: neither is a provider error, so both fall through to `False`.
    # Trying again changes neither — they are the run's own verdict.
    if isinstance(exc, _TRANSIENT):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code in _RETRY_STATUSES


@dataclass
class _Progress:
    """Which attempt is in flight, and how much of the record it has claimed."""

    attempt: int = 1
    #: How many rows already carry a number. Everything after this belongs to
    #: the attempt in flight.
    claimed: int = 0

    def flush(self, hooks, calls: list) -> None:
        """Number every row this attempt produced, and keep what it sent."""
        if (cut_off := hooks.unfinished()) is not None:
            calls.append(cut_off)
        for index in range(self.claimed, len(calls)):
            calls[index] = replace(calls[index], attempt=self.attempt)
        self.claimed = len(calls)


def _why(exc: Exception, attempts: int = 0) -> str:
    """The reason, in a form somebody can act on — scrubbed, because it is
    stored against a task and a provider exception can quote an Authorization
    header."""
    from friday.kernel.ops.redact import scrub

    # `asyncio.wait_for` raises a `TimeoutError` whose `str()` is empty.
    said = "no answer within the request timeout" if isinstance(
        exc, TimeoutError
    ) else scrub(str(exc))
    if attempts > 1:
        return f"gave up after {attempts} attempts: {said}"
    return said

