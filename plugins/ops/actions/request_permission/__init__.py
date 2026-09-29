"""`ops.request_permission`: they want to be let in somewhere.

Recognition from board `domains-plug-in` ticket 02 (its stub). The contract
is today's graph as a ceiling — ask for what is missing, or hand over to the
operator, who grants access; no agent, no toolset. Its numbers are starting
values: no decision fixes them. Registered beside the DAG task type of the
same name until build-the-spine ticket 16.
"""

from __future__ import annotations

from friday.sdk import Action, ActionContract, Limits, Recognition

__all__ = ["ACTION"]

ACTION = Action(
    name="ops.request_permission",
    recognition=Recognition(
        means="They want access: to be let in somewhere, for themselves or a newcomer.",
        pick_when=(
            "a repository, environment, dashboard, channel, key, role or permission",
            'asked outright, or told as a complaint ("I cannot open the staging repo")',
        ),
        not_when=(
            (
                "they ask to raise a limit, a quota or a config value — that is a "
                "problem to trace, not access",
                "backend.trace_problem",
            ),
        ),
        examples=(
            "cho em xin quyền vào repo BE-Midas với ạ",
            "e không mở được Grafana của dev, a add quyền giúp e với",
        ),
    ),
    contract=ActionContract(
        allowed_step_types=frozenset({"ask", "hand_over"}),
        allowed_agents=frozenset(),
        allowed_toolsets=frozenset(),
        constraints=(),
        approval_policy="the operator grants access; Friday only hands over",
        acceptance_template="who needs access to what, handed to the operator",
        limits=Limits(max_replans=0, max_steps=2),
    ),
)
