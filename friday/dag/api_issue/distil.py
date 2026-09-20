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
    #: Whether anything in what survived is an ERROR/WARN line.
    has_error: bool
    #: Whether anything in what survived looks like a stack frame.
    has_stack: bool
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
    #: histogram — counts, not lines — reaching `≤ 12 lines` in context; the
    #: histogram is ticket 05's, and this is the part of it a cap can do:
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

    # Surroundings, in one pass over what was already chosen. A line that is
    # both decisive and somebody else's surroundings stays decisive.
    kept |= decisive
    for i in sorted(decisive):
        for j in range(max(0, i - context_lines), min(len(lines), i + context_lines + 1)):
            kept.add(j)

    not_checked: list[str] = []
    if dropped_others:
        not_checked.append(
            f"{dropped_others} more error lines in the window belong to other "
            f"requests and were not read"
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
        has_error=any(_LOUD.search(line) for line in survived),
        has_stack=any(_FRAME.search(line) for line in survived),
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
