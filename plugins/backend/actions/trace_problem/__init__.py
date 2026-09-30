"""`backend.trace_problem`: something of ours failed, and they want to know why.

Recognition from board `domains-plug-in` ticket 02 (its stub); the contract's
shape from tickets 01, 15 and 17, its toolsets and limits from ticket 03's
stub — starting values, not measured. Runs on the spine since build-the-spine
ticket 14, with `acknowledge` (today's `ack_text`) and `planning` (ticket 15's
first plan, as prose); the DAG task type of the same name stays registered,
unreachable, until ticket 16.
"""

from __future__ import annotations

from friday.sdk import Action, ActionContract, Limits, Recognition
from plugins.backend.actions.trace_problem.acknowledge import acknowledge

__all__ = ["ACTION"]

ACTION = Action(
    name="backend.trace_problem",
    recognition=Recognition(
        means="Something of ours ran and what came back has to be checked: it "
        "failed, looks wrong or incomplete, or they offer a cause or a fix that "
        "must be verified before anyone acts on it.",
        pick_when=(
            "a real request, response, log or error is attached — even when the "
            "words only ask where something is or how it works",
            "a link to a ticket or an issue (Jira, GitHub, GitLab) they want checked: "
            "the problem is written up behind the link",
            'a symptom with no name yet ("bought the plan at 15:00, still no coins")',
            'they guess the cause ("khả năng cao là…", "Claude bảo là…"): a '
            "guess is not an answer, it has to be traced",
            "they ask to raise a limit or change a config because something stopped "
            "working: find the real cause before changing anything",
        ),
        not_when=(
            (
                "they cannot get in somewhere, even said as a problem",
                "ops.request_permission",
            ),
            (
                "a pure question with nothing they ran attached",
                "backend.answer_question",
            ),
        ),
        examples=(
            "a ơi bị lỗi api rồi",
            "a xem giúp e request này với ạ [image: postman.png]",
            "e mua gói sub lúc 15h mà 30 phút sau vẫn chưa được cộng coin a ạ",
            "a ơi item ai_edit không có thumbnail, đoạn nào trả về cái này vậy a "
            '{"statusCode":200,"data":[{"key":"ai_edit","thumbnail":null}]}',
            "a nâng free limit gen ảnh lên 100 lượt giúp e với, user kêu hết lượt rồi",
            "gọi POST /v1/videos bị 412, bên AI bảo chắc do hết quota provider, a check giúp e",
            "a xem giúp e issue này với https://github.com/apero/reelme-api/issues/412",
        ),
    ),
    contract=ActionContract(
        allowed_step_types=frozenset({"agent", "ask", "hand_over", "draft"}),
        allowed_agents=frozenset({"backend.diagnose"}),
        allowed_toolsets=frozenset(
            {"backend.logs", "backend.code", "core.memory", "core.skills"}
        ),
        constraints=("every ref points at a line that was read",),
        approval_policy="a reply waits for the operator's approval",
        acceptance_template="a cause; conclusive ⇒ at least one ref and one rival ruled out",
        limits=Limits(max_replans=2, max_steps=4),
    ),
    acknowledge=acknowledge,
    planning="Usually two steps: one backend.diagnose step, then a draft that "
    "reads it. The brief says what the step "
    "must establish — which field, rule or call rejected the request — and what "
    "the reporter gave (ids, endpoints, times); how to read is the agent's own. "
    "When Intake left the service unresolved, name its candidates in the brief. "
    "Ask the reporter up front only when there is nothing to search for: no "
    "endpoint, no error, no id.",
)
