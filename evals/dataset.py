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


def build_frozen_set(
    *,
    confirmed: list[tuple[str, str]],
    seed: Sequence[tuple[str, str]] = (),
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
    """
    excluded_text = {text for text, _ in excluded}
    seen: dict[str, str] = {}
    for text, kind in [*confirmed, *seed]:
        if text in excluded_text:
            continue
        seen[text] = kind
    return [Example(text=text, expected=kind) for text, kind in seen.items()]


def load_jsonl(path: Path) -> list[Example]:
    examples = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        examples.append(Example(text=row["text"], expected=row["expected"]))
    return examples


def write_jsonl(path: Path, examples: list[Example]) -> None:
    """`ensure_ascii=False`: this file is reviewed as a diff, and real
    reports are bilingual, so an escaped `\\u1ebft` in place of `ế` would
    make every Vietnamese example unreadable in review."""
    lines = [
        json.dumps({"text": e.text, "expected": e.expected}, ensure_ascii=False)
        for e in examples
    ]
    path.write_text("\n".join(lines) + "\n" if lines else "")
