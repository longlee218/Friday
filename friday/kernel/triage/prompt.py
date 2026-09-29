"""What triage's prompt looks like, and from what it is assembled.

Assembled from the shared section builders, like every other agent — that is
what makes "the same format" true rather than intended. The stable half is
who it is, the job, and the operator's vouched-for examples, appended to
instructions rather than sent per call because examples that moved per call
would cost the cache hit on everything after them.

Over the 200-line target because most of it is prompt text a model reads
(`JOB`, `THINKING`, `REMINDERS`), kept whole in one place.

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

from collections.abc import Iterable, Sequence

from friday.kernel.harness.instruction_prompt import (
    assemble,
    channel_derived,
    clarification_system,
    conversation,
    critical_reminder,
    few_shot,
    job,
    labels,
    role,
    thinking_style,
    trust_boundary,
)
from friday.kernel.domain.tasks import SKIP
from friday.kernel.triage.context import LightContext
from friday.sdk.action import Action

__all__ = ["SKIP_EXAMPLES", "build_input", "build_instructions", "declared_examples"]


#: The job. Whole sentences to a model, so no comments inside — anything that
#: must not ship lives up here. Nothing below names a field or a tool: the
#: tools carry their own docstrings, and that schema is the real contract.
JOB = """You decide what a chat message is. Nothing else.

Answer once, with the label that fits and how certain you are of it. The label
is one of a fixed set; nothing outside that set is an answer.

Do not copy values out of the message, do not summarise it, do not answer it.
Something else reads the message for what it contains — your job is the label
and your confidence in it."""

#: How to arrive at the label. The first two are ordered because reading
#: before deciding is what stops a keyword in the first line settling it; the
#: labels themselves have no order (board `domains-plug-in`, ticket 02): each
#: action's `not_when` pairs decide between two, and the core names none.
THINKING = [
    "Start from why this message is in front of you. Everything else in the "
    "channel was filtered out: what reaches you was addressed to this desk — "
    "it tagged us, it is a direct message, or it answers something we asked. "
    "Somebody wanted something from us. Work is the normal case; skip is the "
    "exception.",
    "Read the whole turn before deciding. The useful part often comes second "
    "— a curl, a log, an attachment, a response body, an error code — and the "
    "opening line is only a greeting.",
    "It is skip only when it asks for nothing at all — thanks, a greeting, a "
    "joke, salary, personal matters, or somebody simply telling us what they "
    "did. Being short, vague or wordless is not a reason to skip: somebody "
    "who tags this desk and says little still wants something.",
    "Weigh every label below against the message; no label comes first. "
    "Where a label says \"not when … → another label\", that pair decides "
    "between the two.",
    "If two labels still fit, pick the one the person would recognise as "
    "their own problem, and lower your confidence to say so. Only answer with "
    "low confidence when you are genuinely unsure; a clear message deserves a "
    "high one.",
]

#: What `skip` means. The core's own label: no plugin owns it, and it is the
#: same on every install, so it renders last, after every action's.
SKIP_MEANS = (
    "Nobody is asking you for anything — social talk, thanks, salary, "
    "personal matters, or people talking among themselves."
)

#: The core's own `skip` examples, shown after every action's declared ones.
#: Nothing here may appear in `evals/triage.jsonl` (a suite test).
SKIP_EXAMPLES = ("ok a, e hiểu rồi ạ", "chúc mừng a lên chức ạ")


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


def declared_examples(actions: Iterable[Action]) -> list[tuple[str, str]]:
    """What the classifier is shown before any mark: each action's declared
    examples (sorted by action, as the labels are), then the core's `skip`
    ones. `evals/build_triage_set.py` excludes exactly these from the set."""
    out = [
        (text, action.name)
        for action in sorted(actions, key=lambda a: a.name)
        for text in action.recognition.examples
    ]
    return out + [(text, SKIP) for text in SKIP_EXAMPLES]


def _examples(
    actions: Iterable[Action], confirmed: Sequence[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Examples add up: the declared ones, then the operator-confirmed rows.
    The declared never drop out as confirmed rows accumulate, so a new action
    is not outweighed and the prefix stays stable. A confirmed row whose text
    is already shown is not shown twice."""
    out = declared_examples(actions)
    shown = {text.strip() for text, _ in out}
    out += [(text, label) for text, label in confirmed if text.strip() not in shown]
    return out


def build_instructions(
    *,
    actions: Iterable[Action] = (),
    examples: Sequence[tuple[str, str]] = (),
) -> str:
    """Who it is, the job, how to think, the labels, what it was shown, what
    not to get wrong — in that order, because the order is how much each part
    moves.

    `actions` are the registered ones (their `Recognition` is the labels'
    whole meaning — the answer schema carries none). `examples` are the
    operator-confirmed rows; the declared and `skip` examples are added here.
    Both change at startup rather than per call, so they sit after everything
    stable and before the reminder that closes.
    """
    actions = list(actions)
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
        labels(actions, last=(SKIP, SKIP_MEANS)),
        # Only classifications the operator marked *right* follow the
        # declared ones. An example nobody looked at teaches the classifier
        # its own habits, and the drift has no floor because every generation
        # is drawn from the last one's output.
        few_shot(_examples(actions, examples), verdict="what it turned out to be"),
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
