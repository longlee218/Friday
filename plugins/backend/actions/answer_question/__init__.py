"""`backend.answer_question`: does the code running now do X, and how.

Recognition and contract from board `domains-plug-in` ticket 06 §9–10 (as
amended by 17, and §9 on 2026-09-29: a pure question only — anything they
ran, attached, is `trace_problem`). Registered beside the DAG task type of the same name until
build-the-spine ticket 15 moves the work onto the spine.
"""

from __future__ import annotations

from friday.sdk import Action, ActionContract, Limits, Recognition

__all__ = ["ACTION"]

ACTION = Action(
    name="backend.answer_question",
    recognition=Recognition(
        means="A pure question about our code, API, config or docs — whether it "
        "does something, how, or where it is written — with nothing they ran attached.",
        pick_when=(
            '"đã làm … chưa", "có dùng … không", "hoạt động thế nào", "ở đâu"',
            "no request, response, log or error of theirs in the turn",
        ),
        not_when=(
            (
                "a real request, response, log or error is attached, or a ticket or issue "
                "link to check, even with a how or where question — what came back has to "
                "be checked",
                "backend.trace_problem",
            ),
        ),
        examples=(
            "does batch generation use the free-gen path?",
            "is the webhook secured by a token yet?",
            "does the Printful position calculation do the image crop?",
            "api /v1/plans trả về những field gì vậy a, e chưa gọi thử",
        ),
    ),
    contract=ActionContract(
        allowed_step_types=frozenset({"agent", "ask", "hand_over", "draft"}),
        allowed_agents=frozenset({"backend.explain"}),
        allowed_toolsets=frozenset({"backend.code", "backend.docs"}),
        constraints=("every ref points at a line that was read",),
        approval_policy="a reply waits for the operator's approval",
        acceptance_template="a verdict; conclusive ⇒ at least one ref; no + conclusive ⇒ "
        "a ref to where it would have been done",
        limits=Limits(max_replans=1, max_steps=3),
    ),
)
