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

__all__ = ["Example", "build_frozen_set", "load_jsonl", "write_jsonl"]


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
    for row in [*confirmed, *seed]:
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
