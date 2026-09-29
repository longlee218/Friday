"""What `backend.diagnose` is told, and what it is shown.

Beside the agent (`agents/diagnose.py`) rather than in `friday/kernel/harness/`,
because a plugin owns its agents' prompts; moved here from `graph/` in
build-the-spine ticket 14, the DAG node reads it from here until ticket 16.
Assembled from the same sections every
other agent's prompt is, so a change to how an agent is addressed reaches
this one too — a prompt written by hand here is the fourth unwrapped prompt
this repository has had to go back and wrap.

**No `<soul>`.** `Diagnose` does not speak for the operator: it writes a
cause and a confidence for the operator to read, and `Explain` — ticket 06 —
is the one that writes to the reporter. An agent told to be warm writes a
diagnosis that is warm, which is not a property a diagnosis should have.
"""

from __future__ import annotations

from typing import Any

from friday.sdk.prompt import (
    assemble,
    critical_reminder,
    job,
    role,
    thinking_style,
    trust_boundary,
)

__all__ = ["build_instructions", "build_reads_input"]

#: The dossier mode's job: everything was read in advance, the model has no
#: tools. Only the DAG node without a toolset renders it; it goes with the
#: DAG in build-the-spine ticket 16.
JOB = """
You are given what a reporter sent, the log lines that survived a
recall-first cut, and the source around any stack frame those lines named.
Say what caused the failure.

Everything you are shown is evidence or it is nothing. You have no tools on
this path: you cannot read another file, run another query, or check a
database. So an answer you cannot point at a line for is not an answer you
may give — say it is not conclusive and name what would settle it.

Every line you are shown has an id in its left margin, like `L12`. Point at
lines by those ids. Do not copy the line's text into your answer: the id is
what is checked, and code puts the text back.
""".strip()

#: The job `backend.diagnose` runs on the spine: it reads for itself. The
#: reading procedure (`READS`) is part of it — how to fetch evidence is the
#: job, not a way of weighing it (build-the-spine ticket 14 review).
JOB_READS = """
You are given what a reporter sent and where the service lives.
Nothing has been read for you: you read the logs and the code yourself, with
your tools, then say what caused the failure.

Everything a tool shows you is evidence, and nothing else is. A line only
becomes citable once a tool has shown it: every line comes back with an id in
its left margin, like `L12`. Point at lines by those ids. Do not copy the
line's text into your answer: the id is what is checked, and code puts the
text back. An answer you cannot point at a line for is not an answer you may
give — say it is not conclusive and name what would settle it.

How to read:
""".strip()

THINKING = [
    "Read the error line first. A stack trace names a throw site; a 4xx with "
    "a domain message names a rule that refused.",
    "Ask whose error it is. A downstream service speaking through this "
    "service's exception filter is not this service's bug.",
    "Point at the evidence. Every `ref` you give is a line id — `L12` — "
    "off the margin of what you were shown.",
    "Say what you could not check — a window that did not reach back far "
    "enough, code read at HEAD rather than the running tag — and put what "
    "you would read next in `next_checks`.",
    "Name what else it could have been, and what rules that out. Put it in "
    "`alternatives_rejected` with the line id that shows it. The cause you "
    "reached first and the cause that survived a rival read the same to "
    "whoever gets the report; only one of them was weighed.",
    "Decide whether it is conclusive. Conclusive means the evidence shown "
    "would convince somebody who did not trust you.",
]

REMINDERS = [
    "A `ref` that names no line you were shown is refused, and the whole "
    "answer with it. Ids only — `L12`, not the line's text.",
    "Which version of the code you were shown is stated beside it — the "
    "running tag, or the clone's HEAD when that could not be resolved. "
    "Do not assume it is the one that produced the log line unless it says so.",
    "`conclusive: true` with an empty `alternatives_rejected` is refused, "
    "and the whole answer with it. Saying it is conclusive is saying you "
    "considered the alternatives, so show one.",
    "`conclusive: false` costs nothing. A confident wrong cause is asserted "
    "in the operator's name to their own team.",
]


#: The reading procedure, rendered inside `JOB_READS` (spec, v3.3): which
#: tool first, when to widen, when code is worth reading.
READS = [
    "Start with `read_log`, using the most "
    "specific thing the report gives you — a correlationId names one "
    "request, an id names one user, an endpoint names everyone who called "
    "it.",
    "Found nothing? Widen `minutes_back`, or search a different string. "
    "Found nothing twice? That is an answer about the request, and saying "
    "so beats a cause built from the endpoint's name.",
    "A stack frame in what you read is worth `read_code`, with `repo` the "
    "project named under where the service lives. A frame in "
    "`node_modules` is somebody else's code and is not. No frame? "
    "`search_code` finds where a route or a symbol is written.",
]


def build_instructions(*, reads: bool = False) -> str:
    """Who it is, the job, how to read, what not to get wrong.

    `reads` is the v3.3 mode: the model fetches its own evidence instead of
    being handed a dossier. Two sets of instructions rather than one that
    hedges, because the half that changes is the half about where evidence
    comes from, and a prompt saying "the lines you were shown" to a model
    that was shown nothing is a prompt it cannot obey.
    """
    said = JOB
    if reads:
        said = JOB_READS + "\n" + "\n".join(f"- {step}" for step in READS)
    return assemble(
        role("Friday", "a backend diagnostician", "you say what caused a failure"),
        trust_boundary(),
        job(said),
        thinking_style(THINKING),
        critical_reminder(REMINDERS),
    )


def build_reads_input(
    *, report: str, placement: Any, not_checked: tuple[str, ...]
) -> str:
    """The case as metadata: where it lives, and nothing read yet.

    This is the whole of what `Gather` produces under v3.3 — where the
    service runs, which clone holds its code, what the reporter said. The
    evidence is the model's to fetch.
    """
    where = [
        f"environment: {placement.env}",
        f"service: {placement.service}",
    ]
    if placement.app:
        where.append(f"loki app: {placement.app} in {placement.namespace}")
    if placement.pod_pattern:
        where.append(f"pods matching: {placement.pod_pattern}")
    if placement.repo_path:
        where.append(f"repository: {placement.repo_path}")
    if placement.project:
        where.append(f"repo (for the code tools): {placement.project}")
    others = [p.name for p in placement.projects if p.name != placement.project]
    if others:
        where.append(f"the room's other repos: {', '.join(others)}")
    if placement.stack:
        where.append(f"stack: {placement.stack}")

    said = [
        "## What was reported",
        report or "(nothing beyond the parameters)",
        "",
        "## Where this service lives",
        *where,
    ]
    if not_checked:
        said += ["", "## Already known not to have been checked", *not_checked]
    said += [
        "",
        "## Your job",
        "Read what you need with the tools, then answer the shape. Nothing "
        "has been read for you. If you genuinely cannot diagnose this and a "
        "person must take it — the fix needs an action you may not take, or "
        "the case is outside what these tools reach — call `hand_over` with "
        "why, instead of guessing.",
    ]
    return "\n".join(said)
