"""Scoring a diagnosis against what the operator said was true.

Pure and synchronous, like `scoring.py`: nothing here calls a model or
touches a database, so it is unit-tested without either.

**Why this exists, plainly.** A change to the distillation rule, to how a
read is narrowed, or to the answer's shape can make the model's causes
better or worse, and the test suite cannot tell. It checks that code does
what it was told to; there is nothing in it that checks whether the
*diagnosis* improved. Three such changes landed on 2026-09-21 alone, each
verified by one person reading one cause and nodding.

That gap becomes load-bearing under architecture v3.3, where the model
drives its own reads: the same case may then be investigated two ways, and
"the suite is green" says nothing at all about which architecture diagnoses
better.

**No model scores this.** The spec's rule, and it is not squeamishness: "an
LLM critic is an eval variant until its scores agree with the operator's
marks". So the comparison is exact, code-only, and its weakness is stated
below rather than hidden.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Scored", "report", "score"]


@dataclass(frozen=True, slots=True)
class Scored:
    """One captured case, run and compared.

    `said_conclusive` is `None` when the answer never arrived — the refs gate
    or the alternatives gate voided it, or no diagnosis was produced. That is
    a third outcome, not a wrong boolean, and collapsing it into `False`
    would make a run that refused to answer look like a cautious one.
    """

    case: str
    #: Every token the operator said a correct answer must contain was in it.
    cause_found: bool
    #: Which of those were missing. The useful half of a failure.
    missing: tuple[str, ...]
    expected_conclusive: bool
    said_conclusive: bool | None
    #: How many line ids the answer pointed at. Zero with a cause is an
    #: answer that was not built on the evidence.
    refs: int
    #: The answer survived the node's own gates.
    grounded: bool


def score(case: dict, diagnosis: dict | None) -> Scored:
    """Compare one run's diagnosis against the case's labels.

    **Substring matching, and its weakness said out loud.** The operator
    writes `cause_mentions` — a few tokens any correct answer has to contain
    (`categoryId`, `ValidationPipe`) — and this asks whether all of them are
    present, case-insensitively. It cannot tell a right answer phrased
    unusually from a wrong one, and it cannot tell "categoryId is empty"
    from "categoryId is not empty".

    It is still the right first measure: it is deterministic, it costs
    nothing, it disagrees with a person only in ways a person can see and
    correct, and it catches the failure that actually matters — the cause
    drifting off the thing the evidence was about. A judge model would score
    better and would itself need scoring, which is where this loop ends.
    """
    wanted = tuple(str(t) for t in case.get("cause_mentions") or ())
    if diagnosis is None:
        return Scored(
            case=str(case.get("id", "")),
            cause_found=False,
            missing=wanted,
            expected_conclusive=bool(case.get("conclusive", True)),
            said_conclusive=None,
            refs=0,
            grounded=False,
        )
    said = str(diagnosis.get("cause", "")).lower()
    missing = tuple(t for t in wanted if t.lower() not in said)
    return Scored(
        case=str(case.get("id", "")),
        # **An empty `cause_mentions` is not a pass.** A case nobody labelled
        # would otherwise score full marks for any answer at all, which is
        # the one way a growing set could get quieter as it gets weaker.
        cause_found=bool(wanted) and not missing,
        missing=missing,
        expected_conclusive=bool(case.get("conclusive", True)),
        said_conclusive=bool(diagnosis.get("conclusive")),
        refs=len(diagnosis.get("refs") or ()),
        grounded=True,
    )


def report(scored: list[Scored]) -> str:
    """The three numbers ticket 14 asks for, and the rows behind them.

    The rows are printed too, always. A single accuracy figure over a set
    this small is a number that moves by 10% when one case changes its mind,
    and nobody can act on it without seeing which one.
    """
    if not scored:
        return "no cases scored — `data/cases/` is empty"

    n = len(scored)
    causes = sum(1 for s in scored if s.cause_found)
    agreed = sum(
        1 for s in scored
        if s.said_conclusive is not None
        and s.said_conclusive == s.expected_conclusive
    )
    grounded = sum(1 for s in scored if s.grounded)

    lines = [
        f"=== {n} case{'s' if n != 1 else ''} ===",
        f"  cause            {causes}/{n}",
        f"  conclusive agrees {agreed}/{n}",
        f"  answered at all  {grounded}/{n}",
        "",
    ]
    for s in scored:
        mark = "ok  " if s.cause_found else "MISS"
        said = "voided" if s.said_conclusive is None else str(s.said_conclusive)
        lines.append(
            f"  {mark} {s.case:<28} conclusive={said:<7} refs={s.refs}"
            + (f"  missing: {', '.join(s.missing)}" if s.missing else "")
        )
    if n < 10:
        # **No silent smallness.** Three out of three is not a score, and a
        # report that prints a fraction without saying so invites it to be
        # read as one.
        lines += [
            "",
            f"  {n} case{'s' if n != 1 else ''} is a regression check, not a "
            f"score. Ten to twenty make a number worth comparing.",
        ]
    return "\n".join(lines)
