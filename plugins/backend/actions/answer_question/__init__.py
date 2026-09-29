"""`backend.answer_question`: does the code running now do X, and how.

Recognition and contract from board `domains-plug-in` ticket 06 §9–10 (as
amended by 17). Registered beside the DAG task type of the same name until
build-the-spine ticket 15 moves the work onto the spine.
"""

from __future__ import annotations

from friday.sdk import Action, ActionContract, Limits, Recognition

__all__ = ["ACTION"]

ACTION = Action(
    name="backend.answer_question",
    recognition=Recognition(
        means="Whether the current code or docs do something, or how; no behaviour "
        "that happened is reported.",
        pick_when=(
            "\"đã làm … chưa\", \"có dùng … không\", \"hoạt động thế nào\", \"ở đâu\"",
            "no error, curl or response in the message",
        ),
        not_when=(
            ("a real run gave a wrong result, or there is an error code, a curl or a "
             "response — observed behaviour always wins", "backend.trace_problem"),
        ),
        examples=(
            "does batch generation use the free-gen path?",
            "is the webhook secured by a token yet?",
            "does the Printful position calculation do the image crop?",
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
