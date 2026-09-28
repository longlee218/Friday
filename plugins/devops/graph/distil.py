"""Cutting a log down to what a diagnosis can be built from.

**Recall first** (spec, "Every number in this spec is an estimate"): a
request's own lines and every ERROR or WARN line of its correlationId are
never cut. Only the surroundings are. The rule is written this way round
because the cost of the two mistakes is not symmetric — a dossier that is
twice as long costs tokens, and a dossier missing the decisive line costs a
wrong diagnosis asserted in the operator's name.

That is also what makes this a pure function over lines rather than a step
inside the log node. Ticket 16's coverage test names, for each labelled case,
the line the operator says was decisive, and asserts this rule keeps it. A
test cannot do that against a function that needs a cluster.

The slice uses fixed line caps rather than ticket 16's measured token budget
(ticket 00, "what is deliberately out").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Dossier", "distil", "frames"]

#: Lines that are never cut, whatever else is. `WARN` is here with `ERROR`
#: because the operator's own routine reads both — a warning is often the
#: line before the failure, and it is the cheaper of the two mistakes.
#:
#: **Case-insensitive, and that is measured rather than tidy.** Production
#: lines are JSON and spell it `"level":"error"`; dev's `ExceptionFilter`
#: lines spell it `ERROR`. A case-sensitive rule kept the second and cut the
#: first, which is the whole of production — a test said so.
_LOUD = re.compile(r"\b(ERROR|FATAL|WARN|WARNING|EXCEPTION\w*)\b", re.IGNORECASE)

#: The narrower half: what counts as **evidence that something failed**, which
#: `WARN` is not.
#:
#: Measured on production, 2026-09-21. `backend-orbit-v2` emits about seven
#: `WARN` lines a minute of routine engine chatter — "Engine returned an
#: unmappable node status … skipping node", "No credit cost configured …
#: falling back to 15". Nothing is wrong; that is the service working. With
#: `WARN` counting as an error, `has_error` is true in every window this
#: service will ever produce, so `worth_widening` is false in every window
#: too, and the spec's one automatic widening can never fire. A chatty
#: service disarmed it permanently and silently.
#:
#: Two regexes rather than one because the two questions are different and
#: only looked alike. *Is this line worth keeping* — yes, a warning is often
#: the line before the failure, which is why `_LOUD` still carries it. *Did
#: anything fail in this window* — a warning is not evidence either way.
_ERROR = re.compile(r"\b(ERROR|FATAL|EXCEPTION\w*)\b", re.IGNORECASE)

#: What an error code looks like in these services' logs — `ERR19`,
#: `ERR951`, `ERR306`. Measured over 30 days by ticket 16: every one of
#: 2,104 HTTP 500s carries `ERR19`, and `ERR951`/`ERR955` are 58,000 of
#: 90,000 exceptions.
#:
#: **Configuration on the day a second stack disagrees, and not before.**
#: It is an install's shape rather than a room's — the operator's own call
#: about the environment rule was that a *room's* knowledge belongs in rows,
#: and this is neither `example.com` nor a domain. A pattern that matches
#: nothing yields an empty histogram, which is the honest outcome.
_CODE = re.compile(r"\bERR\d+\b")

#: A stack frame, in the two shapes this codebase's services produce: Node's
#: `at fn (/app/src/x.ts:12:3)` and a bare `/app/src/x.ts:12:3`.
_FRAME = re.compile(r"(?P<file>(?:/|\./|[A-Za-z]:\\)[\w./\\-]+\.\w+):(?P<line>\d+)")


@dataclass(frozen=True, slots=True)
class Dossier:
    """What survived the cut, and what the cut noticed about itself.

    `not_checked` is the honest half: a node that widened its window once and
    still found nothing loud says so here, and `Diagnose` renders it rather
    than quietly reasoning over a dossier that never contained an error.
    """

    lines: tuple[str, ...]
    #: How many lines the source offered, before the cut.
    total: int
    #: Whether anything in what survived says something *failed* — an ERROR,
    #: a FATAL, an exception. Not a WARN: see `_ERROR`, where a chatty
    #: production service made that distinction load-bearing.
    has_error: bool
    #: Whether anything in what survived looks like a stack frame.
    has_stack: bool
    #: Every error code in the **window**, counted, most frequent first —
    #: the spec's answer to a noisy window, and how this check reaches its
    #: `≤ 12 lines`. Counts rather than lines because the raw window is a
    #: median 58 lines and up to 885, and because forty repetitions of
    #: `ERR951` say one thing that forty lines say forty times.
    #:
    #: Counted before the cut, not after: counting what survived would be
    #: counting the cut, which says nothing about the window it came from.
    histogram: tuple[tuple[str, int], ...] = ()
    not_checked: tuple[str, ...] = ()

    @property
    def kept(self) -> int:
        return len(self.lines)

    @property
    def worth_widening(self) -> bool:
        """Neither an error nor a stack: the window was probably too narrow.

        Read by the log node, which is the only thing that can act on it —
        widening means asking the source again (spec, "One automatic widening
        of the window"). Said here because this is what looked at the lines.
        """
        return not self.has_error and not self.has_stack

    def text(self) -> str:
        return "\n".join(self.lines)


def distil(
    lines: list[str] | tuple[str, ...],
    *,
    correlation_id: str | None = None,
    #: Identifiers the log line carries that name this request when there is
    #: no correlationId — the endpoint path, a deviceId (D2: "a curl, *or* an
    #: endpoint plus one identifier the log line carries").
    matching: tuple[str, ...] = (),
    #: How many lines either side of a kept line to keep with it. Surroundings
    #: are the only thing this rule is allowed to cut.
    context_lines: int = 2,
    #: The ceiling, applied to surroundings only. A run whose *decisive* lines
    #: alone exceed it returns all of them and says so in `not_checked`,
    #: because dropping one to respect a cap is the mistake this rule exists
    #: to prevent.
    max_lines: int = 400,
    #: How many loud lines that do **not** name this request are worth
    #: carrying. The spec's answer to a noisy window is an error-code
    #: histogram — counts, not lines — reaching `≤ 12 lines` in context. The
    #: histogram is `Dossier.histogram` now; this is the cap beside it:
    #: keep a sample, count the rest, and say so. Without it a busy window
    #: is the ~12,000-token dossier the spec measured and refused.
    other_error_cap: int = 20,
) -> Dossier:
    """Keep every line that names this request or is loud; cut the rest.

    `correlation_id` and `matching` are both matched as plain substrings, not
    parsed: the log is JSON on production and plain text on dev, and a rule
    that has to know which one it is reading is a rule with two failure modes.
    """
    kept: set[int] = set()
    decisive: set[int] = set()
    ours: set[int] = set()
    needles = [n for n in (correlation_id, *matching) if n]
    counted: dict[str, int] = {}
    for line in lines:
        for code in _CODE.findall(line):
            counted[code] = counted.get(code, 0) + 1

    for i, line in enumerate(lines):
        # A stack frame is decisive in its own right, not only when the line
        # around it happens to say ERROR. The frame is what the code node
        # reads next, and a trace whose first line was loud and whose
        # remaining twenty frames were not is the shape that taught this.
        if any(needle in line for needle in needles):
            ours.add(i)
            decisive.add(i)
        elif _LOUD.search(line) or _FRAME.search(line):
            decisive.add(i)

    # Loud lines belonging to *other* requests are the noise the spec's
    # histogram exists to count rather than quote. A sample of them is worth
    # carrying — "Already have transaction" from a downstream service is how
    # the operator recognises a dependency — and the rest is a number.
    others = sorted(decisive - ours)
    dropped_others = 0
    if needles and len(others) > other_error_cap:
        for i in others[other_error_cap:]:
            decisive.discard(i)
        dropped_others = len(others) - other_error_cap

    # **Surroundings belong to this request's lines, not to the sample.**
    # A sampled error from somebody else's request is quoted to show the
    # shape of the window; the two lines either side of it are that other
    # request's story, and they are what pushed the first version to thirteen
    # lines where the spec allows twelve. With no needle at all there is
    # nothing that is "ours", so everything loud keeps its context — that is
    # the case where the surroundings are all the story there is.
    kept |= decisive
    around = ours if needles else decisive
    for i in sorted(around):
        for j in range(max(0, i - context_lines), min(len(lines), i + context_lines + 1)):
            kept.add(j)

    not_checked: list[str] = []
    if dropped_others:
        # "8 of N", never a silent 8: a sample nobody is told is a sample
        # reads as everything, which is the whole failure this rule guards.
        not_checked.append(
            f"{other_error_cap} of {other_error_cap + dropped_others} error "
            f"lines in the window belong to other requests; the rest are in "
            f"the counts above, not quoted"
        )
    if not needles:
        not_checked.append(
            "nothing named this request — no correlationId and no path — so "
            "these are every error in the window, not necessarily yours"
        )
    if len(kept) > max_lines:
        # Drop surroundings, newest-first, until it fits. Never a decisive
        # line: that is the whole rule.
        surrounding = sorted(kept - decisive, reverse=True)
        while len(kept) > max_lines and surrounding:
            kept.discard(surrounding.pop(0))
        if len(kept) > max_lines:
            not_checked.append(
                f"{len(decisive)} lines name this request or are errors, over "
                f"the {max_lines}-line cap — none was dropped, so the dossier "
                f"is longer than the cap allows"
            )

    survived = tuple(lines[i] for i in sorted(kept))
    return Dossier(
        lines=survived,
        total=len(lines),
        has_error=any(_ERROR.search(line) for line in survived),
        has_stack=any(_FRAME.search(line) for line in survived),
        histogram=tuple(
            sorted(counted.items(), key=lambda pair: (-pair[1], pair[0]))
        ),
        not_checked=tuple(not_checked),
    )


def frames(dossier: Dossier) -> list[tuple[str, int]]:
    """Every `file:line` in the dossier, in the order it appears.

    Here rather than in the code node because the pattern that recognises a
    frame is the same one `has_stack` answers with, and two patterns that have
    to agree are two patterns that one day do not.
    """
    found: list[tuple[str, int]] = []
    for line in dossier.lines:
        for match in _FRAME.finditer(line):
            found.append((match.group("file"), int(match.group("line"))))
    return found
