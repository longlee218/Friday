"""Node 2a: tell the reporter their message landed, before the slow part.

The investigation reads a log, reads a clone and calls a model; that is
seconds at best and a minute when a back end is slow. A reporter who has
been told nothing in that time does not know whether anything is happening,
and the commonest thing a person does about silence is ask again.

**Sent without approval, and that is the operator's call** (2026-09-22).
Everything else this graph sends to a reporter waits for a person, because
the risk the approval queue guards is in *answering* — a wrong cause
asserted in the operator's name. This one answers nothing: it promises no
finding, quotes no log line and names no fault. An acknowledgement that
waits for approval is an acknowledgement that arrives after the reply it
was supposed to precede, which is the whole of what it was for.

**After `intake`, never before it.** `intake` always runs (it makes no
model call and hands over on nothing), so in practice this is simply "after
node 0" — the edge gate stays anyway, so a future `intake` that can fail
does not silently acknowledge a case nobody is investigating.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.backend.graph.deps import ApiIssueDeps
from plugins.backend.graph.intake import intake_of
from plugins.backend.placement import Placement
from friday.sdk.workflow import DAGState, Node, envelope
from friday.sdk.outbox import Kind

__all__ = ["ack_text", "acknowledge_node"]

log = logging.getLogger(__name__)


def ack_text(placement: Placement) -> str:
    """What the reporter is told. **Written in code and in one language**,
    which is the room's rather than the system's — the board's own ticket
    wrote this phrase. It becomes configuration on the day a second room
    needs a second language, and not before; a setting nobody has asked for
    is a setting whose default nobody has checked.

    Deliberately without a model. An acknowledgement is worth having because
    it is immediate, and a rewording call is both slower than the thing it
    rewords and one more way for it not to arrive at all.

    **Names the service when `Intake` resolved one; otherwise stays generic**
    (the operator's fog-item call, 2026-09-26, refined 2026-09-27). A service
    still vague at this point is a case with `placement.candidates` set — naming
    the candidate list would read as a robot thinking aloud, and naming the
    `env` ("log của external") is worse: `external` is not a place a reporter
    knows, and the investigation reads code and docs, not only logs, so it can
    still help a case with no matching log environment. So an unresolved case
    gets a plain "đang xem lại vụ này" — no place name, no logs-only claim.
    """
    if placement.service:
        return (
            f"Đang xử lý — mình đang xem log của {placement.service}, "
            "sẽ báo lại khi có kết quả."
        )
    return "Đang xử lý — mình đang xem lại vụ này, sẽ báo lại khi có kết quả."


def acknowledge_node() -> Node:
    """Build node 2a.

    Queues rather than sends: the outbox is the only module that delivers,
    and a node that posted to a chat platform itself would be a second place
    that can, with its own retry and its own idea of what has already gone
    out.
    """

    async def _acknowledge(state: DAGState, deps: ApiIssueDeps) -> Any:
        sender: str = deps.sender
        if not sender:
            # Nothing here is worth failing an investigation over, and a run
            # that says why it stayed quiet is better than one that is quiet
            # about being quiet.
            return envelope(
                "skipped",
                "no sender is configured, so nobody was told this was being "
                "worked on",
            )

        # **Once per task, whatever a resume does.** A graph re-runs from its
        # checkpoint after the reporter answers a question, and a second
        # "đang xử lý" three minutes after the first reads as a stuck robot.
        already = await deps.db.outbound_count(deps.task.id, kind=Kind.ACKNOWLEDGED)
        if already:
            return envelope("skipped", "this task was already acknowledged")

        text = ack_text(intake_of(state["intake"]).domain)
        await deps.db.queue_outbound(
            task_id=deps.task.id,
            conversation=deps.task.conversation,
            kind=Kind.ACKNOWLEDGED,
            sender=sender,
            text=text,
            reply_to=await deps.db.last_mention_in(deps.task.conversation),
        )
        log.info("task %s: acknowledged", deps.task.id)
        return envelope("ok", "", said=text)

    # The adapter hands every api_issue node an `ApiIssueDeps`; `Node.run` is
    # typed `[Deps]` (not generic over the subtype), so the narrower parameter is
    # a known, safe variance gap.
    return Node("acknowledge", _acknowledge)  # type: ignore[arg-type]
