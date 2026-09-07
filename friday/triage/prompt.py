"""What triage's prompt looks like, and from what it is assembled.

Assembled from the shared section builders, like every other agent — that is
what makes "the same format" true rather than intended. The stable half is
who it is, the job, and the operator's vouched-for examples, appended to
instructions rather than sent per call because examples that moved per call
would cost the cache hit on everything after them.

**No voice, deliberately.** Triage's whole output is which tool it called and
a number. There is no sentence a voice could improve, and every word would be
paid for on the highest-volume calls in the system to change nothing — which
is what happened: 79% of this prompt was once instructions for writing replies
it never writes.

**No clarification tool, deliberately.** Triage cannot ask; it picks one of
two tools and stops. `clarification_system(None)` renders nothing, which is
the point — an agent told about a door that is not in the room goes looking
for it.
"""

from __future__ import annotations

from collections.abc import Sequence

from friday.agent.instruction_prompt import (
    assemble,
    describe_skill_system,
    clarification_system,
    conversation,
    critical_reminder,
    few_shot,
    job,
    read_skill_file_system,
    role,
    search_skills_system,
    skill_system,
    thinking_style,
    trust_boundary,
)
from friday.domain.models import InboundEvent

__all__ = ["build_input", "build_instructions"]


#: The job. Whole sentences to a model, so no comments inside — anything that
#: must not ship lives up here. Nothing below names a field or a tool: the
#: tools carry their own docstrings, and that schema is the real contract.
JOB = """You decide what a chat message is. Nothing else.

Call exactly one tool. Which tool you call is the answer; the only thing you
add is how certain you are of it.

Do not copy values out of the message, do not summarise it, do not answer it.
Something else reads the message for what it contains — your job is the label
and your confidence in it."""

#: How to arrive at the label. Ordered because the order is the point: reading
#: before deciding is what stops a keyword in the first line settling it.
THINKING = [
    "Read the whole turn — somebody often says the useful part second.",
    "Ask what the person wants to happen, not which words they used.",
    "Pick the one label that fits; if none fits, say so with low confidence.",
]

#: The two that must not be got wrong, at the end where a model looks again.
REMINDERS = [
    "Exactly one tool call. Not two, not none.",
    "Salary, personal matters and social talk are always skip.",
]

#: Kept as an attribute because tests pin sentences in it.
INSTRUCTIONS = JOB


def build_instructions(
    examples: Sequence[tuple[str, str]] = (),
    skills_catalogue: list[str] | None = None,
) -> str:
    """Who it is, the job, how to think, what it was shown, what not to get
    wrong — in that order, because the order is how much each part moves.

    The examples are the only part that changes between installs, and they
    change at startup rather than per call, so they sit after everything
    stable and before the reminder that closes.
    """
    return assemble(
        role("Friday", "a triage classifier", "you decide what a message is"),
        trust_boundary(),
        job(JOB),
        thinking_style(THINKING),
        clarification_system(None),
        # Triage gets skills like every other agent now. Its own job says
        # "call exactly one tool" and means the classifying one; a skill is
        # something it may read on the way, and the turn for it comes from
        # the harness rather than from the correction budget.
        skill_system(skills_catalogue),
        search_skills_system(bool(skills_catalogue)),
        describe_skill_system(bool(skills_catalogue)),
        read_skill_file_system(bool(skills_catalogue)),
        # Only classifications the operator marked *right*. An example
        # nobody looked at teaches the classifier its own habits, and the
        # drift has no floor because every generation is drawn from the last
        # one's output.
        few_shot(list(examples), verdict="what it turned out to be"),
        critical_reminder(REMINDERS),
    )


def build_input(events: Sequence[InboundEvent]) -> str:
    """The turn, and nothing else: no identity, no date, no task section. The
    whole output is which tool was called and a number, and none of those
    would change it.

    Quoted through the section rather than wrapped round it; `_quoted` in the
    seam says why, and this prompt is one of the two that got it wrong until
    ticket 06.
    """
    return assemble(conversation(list(events), quoted=True))
