"""What an agent step is handed: its brief, over the intake context and what
the steps it reads came to (build-the-spine ticket 14; board
`domains-plug-in` ticket 10 — "every step also gets the intake context").

One core prompt for every agent step; what the agent *is* stays in its own
`instructions`. The sections are `instruction_prompt`'s, like every prompt's.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from friday.kernel.harness.instruction_prompt import (
    assemble,
    base,
    facts,
    said,
    spine_facts,
    spine_task,
)
from friday.sdk.intake import IntakeContext

__all__ = ["agent_input", "known", "reply_brief"]


def known(intake: IntakeContext) -> dict[str, Any]:
    """What Intake found, as a step is shown it."""
    return {
        "reported_at": intake.reported_at,
        "domain": intake.domain,
        "hints": intake.hints,
    }


def agent_input(
    brief: str,
    intake: IntakeContext,
    reads: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    """The first message of an agent step: the request, what Intake found,
    what Friday remembers, what earlier steps found, then the brief."""
    return assemble(
        base(now or datetime.now(UTC)),
        *spine_facts(
            request=intake.request_text,
            known=known(intake),
            memory=intake.memory,
            skills=intake.skills,
            found=dict(reads),
        ),
        spine_task("", brief=brief),
    )


def reply_brief(reply: str) -> str:
    """What a continued step is told: the reporter's reply to its question,
    quoted — the rest is already in its history."""
    return assemble(
        facts("continue", "The reporter replied to your question."),
        said("reply", reply)
        if reply.strip()
        else facts("reply", "(they said nothing new; continue with what you have)"),
    )
