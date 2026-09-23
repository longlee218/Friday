"""The frozen set triage is scored against, and how it is built.

Frozen, not queried (D7): `run_triage_eval.py` reads `triage.jsonl` off disk
and never touches the database itself. A set that re-read the database on
every scoring run would move under the prompt it is scoring — a prompt edit
and a change in what the operator has since marked would land in the same
number with no way to tell which moved it. `build_triage_set.py` is the one
script that reads live data, and it runs only when somebody chooses to
refresh the file by hand.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Example", "build_frozen_set", "load_jsonl", "unfit", "write_jsonl"]


@dataclass(frozen=True, slots=True)
class Example:
    text: str
    expected: str
    #: The turn as multiple messages, (text, is_own) pairs in order -- for a
    #: row that needs to exercise something one line cannot: the ownership
    #: mark, or a real multi-line render. Empty means `text` alone is the
    #: whole turn, which is every row this set held before ticket 09 and
    #: still the common case.
    turn: tuple[tuple[str, bool], ...] = ()


def build_frozen_set(
    *,
    confirmed: list[tuple[str, str]],
    seed: Sequence[tuple[str, str] | tuple[str, str, tuple]] = (),
    excluded: Sequence[tuple[str, str]] = (),
) -> list[Example]:
    """Confirmed verdicts plus a hand-written seed, minus whatever the live
    prompt already shows the model as a few-shot example.

    Excluding is not cosmetic: scoring a classifier against the exact
    sentence it was told the right answer to is not a measurement of
    anything, it is the classifier reading back its own instructions.

    Deduplicated by text: the same report marked right twice, or present in
    both `confirmed` and `seed`, is one row rather than two — a duplicate
    would double its weight in the accuracy figure without saying it does.

    A `seed` entry may be a richer `(text, expected, turn)` triple, when it
    exists to exercise something a single string cannot. `confirmed` never
    is — a stored, operator-marked classification has no notion of a `turn`,
    only the text it read.
    """
    excluded_text = {text for text, _ in excluded}
    seen: dict[str, Example] = {}
    # Seed first, confirmed second, so a real verdict *overwrites* the seed row
    # that stood in for it. This read the other way round — both lists
    # concatenated with `confirmed` in front — and the seed won every
    # collision, which is D19 backwards: the seed exists only where nothing
    # real yet says the same thing. Invisible in the case that made anyone
    # write both, because there the two agree on the label; loud the day the
    # operator marks a seed row's own sentence as something else, which is
    # exactly the correction the refresh exists to carry.
    for row in [*seed, *confirmed]:
        text, kind, turn = row if len(row) == 3 else (*row, ())
        if text in excluded_text:
            continue
        seen[text] = Example(text=text, expected=kind, turn=turn)
    return list(seen.values())


def load_jsonl(path: Path) -> list[Example]:
    examples = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        turn = tuple((text, is_own) for text, is_own in row.get("turn", ()))
        examples.append(
            Example(text=row["text"], expected=row["expected"], turn=turn)
        )
    return examples


def write_jsonl(path: Path, examples: list[Example]) -> None:
    """ensure_ascii=False: this file is reviewed as a diff, and real
    reports are bilingual, so an escaped unicode entity in place of a real
    character would make every Vietnamese example unreadable in review.

    `turn` is omitted entirely when empty, not written as an empty list --
    the sixteen rows that existed before ticket 09 are single strings, and
    giving every one of them a key only two or three rows actually use would
    touch every line in a diff for a feature most rows do not have.
    """
    rows = []
    for e in examples:
        row = {"text": e.text, "expected": e.expected}
        if e.turn:
            row["turn"] = [list(pair) for pair in e.turn]
        rows.append(json.dumps(row, ensure_ascii=False))
    path.write_text("\n".join(rows) + "\n" if rows else "")


def unfit(examples: Sequence[Example]) -> list[str]:
    """Why this set is not fit to score a classifier against — empty if it is.

    **A guard over the committed file, not over the builder** (D17). The file
    is written by hand, by somebody running `build_triage_set.py` when there
    is new data worth freezing in, and then committed. Nothing else notices
    the day a refresh drops every `skip` row, or the day a fourth task type is
    registered and the set goes on scoring three — the accuracy figure stays
    perfectly healthy while the thing it is measuring has changed underneath
    it. A value nothing is scored against is a value nothing protects.

    Sentences rather than a boolean, because the caller is a person reading a
    failed assertion and "unfit" tells them nothing they can act on.

    **What is checked is what D17 asks for and no more.** Rows at the boundary
    between two types, and rows a correct classifier should call `skip`, are
    also required of the set — but "is this row near a boundary" is a
    judgement, and a check that guessed at it would either pass everything or
    refuse rows the operator meant. Those stay the operator's, stated in
    `evals/README.md`. What is here is what code can actually decide: a
    decision with no row at all, a set that never shows the classifier a turn,
    and a row that counts twice.
    """
    from friday.dag import registry

    problems = []

    scored = {e.expected for e in examples}
    for missing in sorted(set(registry.decisions()) - scored):
        problems.append(
            f"no row expects {missing!r} — it is one of the decisions triage "
            f"may reach, so nothing is scoring it"
        )

    if not any(len(e.turn) > 1 for e in examples):
        problems.append(
            "no row carries a turn of more than one message — a turn is what "
            "the classifier is actually shown, and a set of single strings "
            "scores it on a shape it never meets in a real channel"
        )

    counts: dict[str, int] = {}
    for example in examples:
        counts[example.text] = counts.get(example.text, 0) + 1
    for text, count in counts.items():
        if count > 1:
            problems.append(
                f"{text!r} appears {count} times — a duplicate doubles its own "
                f"weight in the accuracy figure without saying it does"
            )

    return problems
