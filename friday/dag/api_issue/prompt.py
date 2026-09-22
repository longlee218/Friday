"""What `Diagnose` is told, and what it is shown.

Beside the node rather than in `friday/agent/`, because a graph owns its own
prompts (`friday/dag/__init__.py`). Assembled from the same sections every
other agent's prompt is, so a change to how an agent is addressed reaches
this one too — a prompt written by hand here is the fourth unwrapped prompt
this repository has had to go back and wrap.

**No `<soul>`.** `Diagnose` does not speak for the operator: it writes a
cause and a confidence for the operator to read, and `Explain` — ticket 06 —
is the one that writes to the reporter. An agent told to be warm writes a
diagnosis that is warm, which is not a property a diagnosis should have.
"""

from __future__ import annotations

from friday.agent.instruction_prompt import (
    assemble,
    critical_reminder,
    job,
    role,
    thinking_style,
    trust_boundary,
    user_input,
)

__all__ = ["build_input", "build_instructions", "numbered"]

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

THINKING = [
    "Read the error line first. A stack trace names a throw site; a 4xx with "
    "a domain message names a rule that refused.",
    "Ask whose error it is. A downstream service speaking through this "
    "service's exception filter is not this service's bug.",
    "Point at the evidence. Every `ref` you give is a line id — `L12` — "
    "off the margin of what you were shown.",
    "Say what you could not check. The log window, the code version and "
    "everything outside them are already listed for you — add what you "
    "noticed on top of it.",
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


def build_instructions() -> str:
    """Who it is, the job, how to read, what not to get wrong."""
    return assemble(
        role("Friday", "a backend diagnostician", "you say what caused a failure"),
        trust_boundary(),
        job(JOB),
        thinking_style(THINKING),
        critical_reminder(REMINDERS),
    )


def numbered(dossier: str, code: str) -> tuple[str, str, dict[str, str]]:
    """Both bodies with an id on every line, and the map code keeps.

    **Pointers, not quotes** (spec, measured 2026-09-18, ticket 16): asked to
    quote a JSON log line verbatim the configured model succeeded 32 times in
    40, and every failure put the line's own keys into the answer; asked to
    point at a line by an id it succeeded 20 times in 20. So the model names
    a line and this process supplies the text — which also means the check is
    an exact lookup rather than a fuzzy match on a rewrapped quote.
    """
    index: dict[str, str] = {}
    rendered: list[str] = []
    for body in (dossier, code):
        lines = []
        for raw in body.splitlines():
            if not raw.strip():
                lines.append("")
                continue
            ref = f"L{len(index) + 1}"
            index[ref] = raw
            lines.append(f"{ref} | {raw}")
        rendered.append("\n".join(lines))
    return rendered[0], rendered[1], index


def build_input(
    *,
    report: str,
    dossier: str,
    code: str,
    not_checked: tuple[str, ...],
    histogram: tuple[tuple[str, int], ...] = (),
    codes: dict[str, str] | None = None,
) -> str:
    """The case, as one prompt: what was reported, what the log said, what the
    code says, and what nobody looked at.

    **Every part of it is quoted** (finding D). Log lines, source and the
    reporter's own words are all reporter-influenced text — a log line
    carries whatever somebody got the service to print — and this agent is
    one that writes to memory in a later ticket. Quoting is what keeps a
    crafted log line a line of data rather than an instruction.
    """
    parts = [
        "## What was reported",
        user_input(report) or "(nothing)",
        "",
        "## Log lines that survived the cut",
        user_input(dossier) or "(none — the log was not read, or held nothing)",
        "",
        "## Source around the stack frames",
        user_input(code) or "(none — no frame named a file in the clone)",
    ]
    if histogram:
        # Counts, not lines, and they are evidence of a different kind: a
        # code appearing 40 times in the window is background, and the one
        # appearing once beside this request is not. Unquoted because it is
        # this process's own arithmetic over what it read, not something a
        # reporter wrote.
        parts += [
            "",
            "## Every error code in the window, counted",
            *(f"- {code}: {n}" for code, n in histogram),
        ]
    if codes:
        # The repository's own document, not the model's recollection of
        # what a code means. `ERR19` is on every one of this service's HTTP
        # 500s, so without this the number is all there is.
        parts += [
            "",
            "## What those codes mean, from the repository",
            *(f"- {code}: {meaning}" for code, meaning in sorted(codes.items())),
        ]
    if not_checked:
        parts += [
            "",
            "## Not checked",
            *(f"- {line}" for line in not_checked),
        ]
    return "\n".join(parts)
