"""What `backend.trace_problem` tells the reporter before the slow part — the
action's `acknowledge` hook, queued by the spine once per task, without
approval (board `domains-plug-in` ticket 15 §1–2; build-the-spine ticket 14).

**Sent without approval, and that is the operator's call** (2026-09-22). The
risk the approval queue guards is in *answering*; this answers nothing — it
promises no finding, quotes no log line and names no fault — and one that
waited for approval would arrive after the reply it was meant to precede.
"""

from __future__ import annotations

from friday.sdk.intake import IntakeContext
from plugins.backend.placement import Placement

__all__ = ["ack_text", "acknowledge"]


def acknowledge(context: IntakeContext) -> str:
    """`Action.acknowledge`: the text for this case's placement."""
    return ack_text(context.domain)


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
