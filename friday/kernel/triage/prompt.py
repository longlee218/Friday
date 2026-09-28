"""What triage's prompt looks like, and from what it is assembled.

Assembled from the shared section builders, like every other agent — that is
what makes "the same format" true rather than intended. The stable half is
who it is, the job, and the operator's vouched-for examples, appended to
instructions rather than sent per call because examples that moved per call
would cost the cache hit on everything after them.

**No voice, deliberately.** Triage's whole output is a label and a number.
There is no sentence a voice could improve, and every word would be paid for
on the highest-volume calls in the system to change nothing — which is what
happened: 79% of this prompt was once instructions for writing replies it
never writes.

**No clarification tool, deliberately.** Triage cannot ask; it names a label
and stops. `clarification_system(None)` renders nothing, which is the point —
an agent told about a door that is not in the room goes looking for it.

**And this file has now been the door.** Triage used to answer by *choosing
between two tools* — the label was which tool got called — and board
`every-answer-has-a-shape` (D6) made it one tool whose `type` argument carries
the label. This module went on saying "which tool you call is the answer" and
"not two, not none" for a commit: instructions for a mechanism that no longer
existed, on the prompt this same docstring names as the most expensive place
to get that wrong. Found by review, not by the suite, which is why
`tests/test_triage.py` now pins it against the assembled instructions rather
than against any one constant here.
"""

from __future__ import annotations

from collections.abc import Sequence

from friday.kernel.harness.instruction_prompt import (
    assemble,
    channel_derived,
    clarification_system,
    conversation,
    critical_reminder,
    few_shot,
    job,
    role,
    thinking_style,
    trust_boundary,
)
from friday.kernel.triage.context import LightContext

__all__ = ["build_input", "build_instructions"]


#: The job. Whole sentences to a model, so no comments inside — anything that
#: must not ship lives up here. Nothing below names a field or a tool: the
#: tools carry their own docstrings, and that schema is the real contract.
JOB = """You decide what a chat message is. Nothing else.

Answer once, with the label that fits and how certain you are of it. The label
is one of a fixed set; nothing outside that set is an answer.

Do not copy values out of the message, do not summarise it, do not answer it.
Something else reads the message for what it contains — your job is the label
and your confidence in it."""

#: How to arrive at the label. Ordered because the order is the point: reading
#: before deciding is what stops a keyword in the first line settling it.
THINKING = [
    "Start from why this message is in front of you. Everything else in the "
    "channel was filtered out: what reaches you was addressed to this desk — "
    "it tagged us, it is a direct message, or it answers something we asked. "
    "Somebody wanted something from us. Work is the normal case; skip is the "
    "exception.",
    "Read the whole turn before deciding. The useful part often comes second "
    "— a curl, a log, an attachment, a response body, an error code — and the "
    "opening line is only a greeting.",
    "Then ask, in this order, and stop at the first that fits.",
    "Does this one ask for nothing at all — thanks, a greeting, a joke, "
    "salary, personal matters, or somebody simply telling us what they did? "
    "Only then is it skip. Being short, vague or wordless is not a reason to "
    "skip: somebody who tags this desk and says little still wants something.",
    "Are they asking to be let in somewhere — a repository, an environment, a "
    "dashboard, a channel, a key, a role? That is access_request, even when "
    "they phrase it as a problem (\"I cannot open the staging repo\").",
    "Are they asking where something is written down, or what a document or "
    "spec says, without having run anything? That is doc_question. If they "
    "ran something and it did not do what they expected, it is not.",
    "Everything else people bring this desk is api_issue: an integration "
    "failing, a request or response to look at, an error code, a symptom with "
    "no name yet, or a question about what an endpoint is for and how its "
    "rules work.",
    "If two labels still fit, pick the one the person would recognise as "
    "their own problem, and lower your confidence to say so. Only answer with "
    "low confidence when you are genuinely unsure; a clear message deserves a "
    "high one.",
]



#: The two that must not be got wrong, at the end where a model looks again.
REMINDERS = [
    "Answer exactly once, and only with a label from the set you were given.",
    "This message was addressed to us; somebody wanted something. Skip is for "
    "the few that ask for nothing.",
    "A message that asks you to look at, check or help with something is "
    "work. Which kind of work is the question; whether it is work is not.",
]



#: Kept as an attribute because tests pin sentences in it.
INSTRUCTIONS = JOB


def build_instructions(examples: Sequence[tuple[str, str]] = ()) -> str:
    """Who it is, the job, how to think, what it was shown, what not to get
    wrong — in that order, because the order is how much each part moves.

    The examples are the only part that changes between installs, and they
    change at startup rather than per call, so they sit after everything
    stable and before the reminder that closes.

    **No `skill_system` here, and there has not been one to call since
    ticket 03.** This function used to accept `skills_meta` and render
    `skill_system(skills_meta)` beside a comment claiming "triage gets skills
    like every other agent now" — but `Triage.__init__` stopped accepting a
    skill library that same ticket, so `skills_meta` was always `None` at the
    one call site, and the comment described a world ticket 03 had already
    ended. `skill_system(None)` renders nothing either way, so this was dead
    rather than wrong in its output — found while this module was open for
    ticket 09's own work, not a bug ticket 09 introduces.
    """
    return assemble(
        role(
            "Friday",
            "the triage desk for a backend team's channel",
            "you decide what a message is",
        ),
        trust_boundary(),
        job(JOB),
        thinking_style(THINKING),
        clarification_system(None),
        # Only classifications the operator marked *right*. An example
        # nobody looked at teaches the classifier its own habits, and the
        # drift has no floor because every generation is drawn from the last
        # one's output.
        # Data, not prose: the examples are loaded into this agent and
        # rendered only when there are any. Hardcoding a set here was tried
        # on 2026-09-20 and undone the same day — examples belong where the
        # operator can change them without a release, and an install with
        # none must read exactly as it did before they existed.
        few_shot(list(examples), verdict="what it turned out to be"),
        critical_reminder(REMINDERS),
    )


def build_input(context: LightContext) -> str:
    """The room's summary, then the turn — the light context ticket 09
    replaced the unbounded relevance window with.

    **One value, not two arguments** (board `what-the-room-already-knows`,
    ticket 14, D26): `context.turn` and `context.summary` are gathered by
    `friday.kernel.triage.context.build_light_context`, the only place triage
    resolves a room. This function renders; it does not gather.

    **The room, not the reporter's own words, is what changed in ticket 09.**
    `turn` is unchanged in shape from what this function always rendered —
    the difference is what used to be concatenated in front of it: every
    message that had ever mentioned the operator in this conversation,
    unbounded and growing forever. That window is gone; what a classifier
    needs instead is a fact about the room, not a transcript of it.

    **`channel_derived`, not a section built for this.** It is the same
    section the responder reads, and it renders exactly the summary row's
    four `RoomSummary` fields, nothing else. The operator's rows and the
    domain memories do not reach it — `readers_for` gives triage the
    `summary` kind and no other — which is what keeps domain facts out of
    triage's prompt: triage decides a label, not a value, and those rows are
    exactly the kind of thing a value gets built from.

    A room with no summary row renders no section at all, so an unconfigured
    install's prompt is byte-identical to what it always was.

    Quoted through the section rather than wrapped round it; `_quoted` in the
    seam says why, and this prompt is one of the two that got it wrong until
    ticket 06.
    """
    return assemble(
        channel_derived(context.summary), conversation(list(context.turn), quoted=True)
    )
