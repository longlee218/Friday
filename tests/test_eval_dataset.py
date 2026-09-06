"""Ticket 06 — building and reading `evals/triage.jsonl`.

`build_frozen_set` is the D7 guard in code: an eval set built from whatever
is currently shown to the model as a few-shot example would be scored on the
sentence it was already told the answer to, which measures nothing.
"""

from __future__ import annotations

import json

from evals.dataset import Example, build_frozen_set, load_jsonl, write_jsonl


def test_confirmed_and_seed_both_become_examples():
    frozen = build_frozen_set(
        confirmed=[("the api is down", "api_issue")],
        seed=[("anyone want lunch", "skip")],
    )

    texts = {e.text: e.expected for e in frozen}
    assert texts == {"the api is down": "api_issue", "anyone want lunch": "skip"}


def test_a_row_used_as_a_few_shot_example_is_excluded():
    frozen = build_frozen_set(
        confirmed=[("the api is down", "api_issue"), ("ok thanks", "skip")],
        excluded=[("ok thanks", "skip")],
    )

    assert [e.text for e in frozen] == ["the api is down"]


def test_the_same_text_from_both_sources_is_one_row_not_two():
    """Weight, not correctness: a duplicate would count twice toward
    accuracy without saying it does."""
    frozen = build_frozen_set(
        confirmed=[("the api is down", "api_issue")],
        seed=[("the api is down", "api_issue")],
    )

    assert len(frozen) == 1


def test_write_then_load_round_trips(tmp_path):
    examples = [Example(text="the api is down", expected="api_issue")]
    path = tmp_path / "triage.jsonl"

    write_jsonl(path, examples)

    assert load_jsonl(path) == examples


def test_the_file_is_one_json_object_per_line(tmp_path):
    """Not a JSON array — `evals/triage.jsonl`'s own name and this ticket's
    acceptance criterion both say jsonl, and a diff against one array entry
    would touch every line."""
    path = tmp_path / "triage.jsonl"
    write_jsonl(
        path,
        [Example(text="a", expected="skip"), Example(text="b", expected="skip")],
    )

    lines = path.read_text().splitlines()

    assert len(lines) == 2
    assert json.loads(lines[0]) == {"text": "a", "expected": "skip"}


def test_non_ascii_text_is_written_readable_not_escaped(tmp_path):
    """Real reports are bilingual (`CLAUDE.md` cites "token hết hạn rồi" as
    an ordinary one) and this file is meant to be read in a diff — `\\uXXXX`
    escapes would make every Vietnamese example unreadable in review."""
    path = tmp_path / "triage.jsonl"

    write_jsonl(path, [Example(text="token hết hạn rồi", expected="api_issue")])

    assert "hết hạn" in path.read_text()


def test_load_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "triage.jsonl"
    path.write_text('{"text": "a", "expected": "skip"}\n\n')

    assert load_jsonl(path) == [Example(text="a", expected="skip")]
