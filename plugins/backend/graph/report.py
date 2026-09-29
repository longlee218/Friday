"""Node 5: offer the reporter the cause, or hand the task to the operator.

The report file (`data/reports/<task>.md`) and the operator's finding row
that pointed at it went in build-the-spine ticket 14: the board's stored plan
versions and step results replace them (board `domains-plug-in` ticket 15
§3). This node is the DAG path's, unreachable since `trace_problem` runs on
the spine, and goes with the DAG in ticket 16.
"""

from __future__ import annotations

from typing import Any

from friday.sdk.workflow import DAGState, Deps, HandOver, Node, Reply
from plugins.backend.graph.diagnose import diagnosis_of

__all__ = ["brief", "report_node"]


def brief(diagnosis: Any) -> str:
    """What the reporter is offered, once somebody approves it.

    The cause and nothing else — no dossier, no file path, no frame. They
    asked what was wrong with their request; the evidence is the operator's
    to look at, and a local filesystem path means nothing to them and says
    more about this machine than they need.

    `next_checks` is left out on purpose: it is what *this* investigation
    would do next, which is a note to the operator and reads to a reporter
    as a list of things they have been asked to do.
    """
    said = str(getattr(diagnosis, "cause", "")).strip()
    if not getattr(diagnosis, "conclusive", False):
        # Said plainly rather than hedged into the sentence, so nobody has to
        # notice a missing word to know how far this got.
        said += " (chưa kết luận chắc chắn — cần kiểm thêm)"
    return said


def report_node() -> Node:
    """Build node 5. Hands over only when nothing was concluded; with a cause
    it returns a `Reply`, which waits for the operator's approval — the risk
    this queue guards is in *answering*."""

    async def _report(state: DAGState, deps: Deps) -> Any:
        thought = state.get("diagnose", {})
        diagnosis = diagnosis_of(thought)
        if diagnosis is None:
            # **Nothing concluded, so nothing offered to the reporter.**
            said = (
                thought.get("reason", "") if isinstance(thought, dict) else ""
            ) or "nothing was diagnosed"
            return HandOver(said + ". Nothing has been sent to the reporter.")
        return Reply(brief(diagnosis))

    return Node("report", _report)
