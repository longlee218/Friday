"""`backend.trace_problem`: something of ours failed, and they want to know why.

Recognition from board `domains-plug-in` ticket 02 (its stub); the contract's
shape from tickets 01, 15 and 17, its toolsets and limits from ticket 03's
stub — starting values, not measured. Registered beside the DAG task type of
the same name until build-the-spine ticket 14 moves the work onto the spine,
where this folder gains `acknowledge` and `planning`.
"""

from __future__ import annotations

from friday.sdk import Action, ActionContract, Limits, Recognition

__all__ = ["ACTION"]

ACTION = Action(
    name="backend.trace_problem",
    recognition=Recognition(
        means="Something our systems did, or did not do, and they want to know why.",
        pick_when=(
            "an error code, a failing request, a curl, a log, a response body",
            "a symptom with no name yet (\"bought the plan at 15:00, still no coins\")",
            "they ran something and it did not do what they expected",
        ),
        not_when=(
            ("they cannot get in somewhere, even said as a problem", "ops.request_permission"),
            ("nothing failed; they ask how a rule or endpoint works", "backend.answer_question"),
        ),
        examples=(
            "a ơi bị lỗi api rồi",
            "a xem giúp e request này với ạ [image: postman.png]",
            "e mua gói sub lúc 15h mà 30 phút sau vẫn chưa được cộng coin a ạ",
        ),
    ),
    contract=ActionContract(
        allowed_step_types=frozenset({"agent", "ask", "hand_over", "draft"}),
        allowed_agents=frozenset({"backend.diagnose"}),
        allowed_toolsets=frozenset({"backend.logs", "backend.code", "core.memory", "core.skills"}),
        constraints=("every ref points at a line that was read",),
        approval_policy="a reply waits for the operator's approval",
        acceptance_template="a cause; conclusive ⇒ at least one ref and one rival ruled out",
        limits=Limits(max_replans=2, max_steps=4),
    ),
)
