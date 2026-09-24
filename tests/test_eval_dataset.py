"""Ticket 06 — building and reading `evals/triage.jsonl`.

`build_frozen_set` is the D7 guard in code: an eval set built from whatever
is currently shown to the model as a few-shot example would be scored on the
sentence it was already told the answer to, which measures nothing.
"""

from __future__ import annotations

import json

from pathlib import Path

from friday.dag import registry

from evals.dataset import Example, build_frozen_set, load_jsonl, write_jsonl


def test_confirmed_and_seed_both_become_examples():
    frozen = build_frozen_set(
        confirmed=[("the api is down", "devops.api_issue")],
        seed=[("anyone want lunch", "skip")],
    )

    texts = {e.text: e.expected for e in frozen}
    assert texts == {"the api is down": "devops.api_issue", "anyone want lunch": "skip"}


def test_a_row_used_as_a_few_shot_example_is_excluded():
    frozen = build_frozen_set(
        confirmed=[("the api is down", "devops.api_issue"), ("ok thanks", "skip")],
        excluded=[("ok thanks", "skip")],
    )

    assert [e.text for e in frozen] == ["the api is down"]


def test_the_same_text_from_both_sources_is_one_row_not_two():
    """Weight, not correctness: a duplicate would count twice toward
    accuracy without saying it does."""
    frozen = build_frozen_set(
        confirmed=[("the api is down", "devops.api_issue")],
        seed=[("the api is down", "devops.api_issue")],
    )

    assert len(frozen) == 1


def test_write_then_load_round_trips(tmp_path):
    examples = [Example(text="the api is down", expected="devops.api_issue")]
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

    write_jsonl(path, [Example(text="token hết hạn rồi", expected="devops.api_issue")])

    assert "hết hạn" in path.read_text()


def test_load_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "triage.jsonl"
    path.write_text('{"text": "a", "expected": "skip"}\n\n')

    assert load_jsonl(path) == [Example(text="a", expected="skip")]


# --- a multi-message turn (ticket 09) --------------------------------------


def test_a_plain_example_writes_no_turn_key_at_all():
    """Sixteen existing rows are single strings and must stay that way on
    disk — a `"turn": []` key on every line would touch every row in a diff
    for a feature only two or three of them use."""
    path = write_jsonl_to_tmp([Example(text="the api is down", expected="devops.api_issue")])
    assert json.loads(path.read_text().splitlines()[0]) == {
        "text": "the api is down", "expected": "devops.api_issue"
    }


def test_a_multi_message_example_round_trips_its_turn(tmp_path):
    """The turn is (text, is_own) pairs, in order — the shape needed to
    exercise the ownership mark and multi-line rendering, which the sixteen
    single-string rows cannot."""
    example = Example(
        text="whatever the classifier is shown when there is no turn to give",
        expected="devops.api_issue",
        turn=(
            ("api lỗi rồi anh ơi", False),
            ("correlationId nằm trong header x-request-id đó em", True),
        ),
    )
    path = tmp_path / "triage.jsonl"

    write_jsonl(path, [example])

    assert load_jsonl(path) == [example]


def write_jsonl_to_tmp(examples):
    import tempfile
    d = Path(tempfile.mkdtemp())
    path = d / "triage.jsonl"
    write_jsonl(path, examples)
    return path


def test_a_seed_row_can_carry_a_turn():
    """A hand-written seed row may be a richer `(text, expected, turn)` triple
    when it exists to exercise something a single string cannot; `confirmed`
    verdicts never carry one — a stored classification has no such thing."""
    frozen = build_frozen_set(
        confirmed=[],
        seed=[
            (
                "api lỗi rồi anh ơi",
                "devops.api_issue",
                (("api lỗi rồi anh ơi", False), ("curl -X GET /pay trả 500", False)),
            ),
        ],
    )

    (example,) = frozen
    assert example.turn == (
        ("api lỗi rồi anh ơi", False), ("curl -X GET /pay trả 500", False),
    )


def test_a_plain_seed_row_still_works_alongside_a_turn_row():
    frozen = build_frozen_set(
        confirmed=[("the api is down", "devops.api_issue")],
        seed=[("anyone want lunch", "skip")],
    )

    assert {e.text: e.turn for e in frozen} == {
        "the api is down": (), "anyone want lunch": (),
    }


# --- the set has to be fit to score against (board `every-answer-has-a-shape`,
# --- ticket 02, D17/D18) ----------------------------------------------------


def test_a_real_verdict_beats_a_seed_row_saying_the_same_thing():
    """D19: the seed is kept only where nothing real yet says the same thing.
    It was the other way round — `confirmed` and `seed` were concatenated and
    written into one dict by text, so the seed row overwrote the operator's
    own marked verdict whenever both existed. Invisible, because the two
    agree on the label in the case that made anyone write both.
    """
    frozen = build_frozen_set(
        confirmed=[("the api is down", "devops.api_issue")],
        seed=[("the api is down", "skip")],
    )

    (example,) = frozen
    assert example.expected == "devops.api_issue", "the seed overwrote a real verdict"


def test_a_set_missing_a_decision_says_which_one():
    from evals.dataset import unfit

    said = unfit([Example(text="the api is down", expected="devops.api_issue")])

    assert any("skip" in line for line in said)
    assert any("access_request" in line for line in said)


def test_a_set_with_no_multi_message_turn_says_so():
    """A turn is what the classifier is actually shown (D17). A set of single
    strings scores it on a shape it never meets in the channel."""
    from evals.dataset import unfit

    every_decision = [
        Example(text=f"about {decision}", expected=decision) for decision in registry.decisions()
    ]

    assert any("turn" in line for line in unfit(every_decision))


def test_a_duplicated_text_is_reported_because_it_doubles_its_own_weight():
    from evals.dataset import unfit

    twice = [
        Example(text="the api is down", expected="devops.api_issue"),
        Example(text="the api is down", expected="devops.api_issue"),
    ]

    assert any("the api is down" in line for line in unfit(twice))


def test_a_set_that_covers_everything_is_reported_as_fit():
    from evals.dataset import unfit

    covering = [
        Example(text=f"about {decision}", expected=decision) for decision in registry.decisions()
    ]
    covering.append(
        Example(
            text="a burst",
            expected="devops.api_issue",
            turn=(("api lỗi rồi", False), ("correlationId là 3f7a1e22", False)),
        )
    )

    assert unfit(covering) == []


def test_the_frozen_set_this_repo_ships_is_fit_to_score_against():
    """The guard over the file itself, not over the function — which is the
    point of it. `build_frozen_set` is run by hand and its output is committed,
    so nothing else notices the day a refresh drops every `skip` row or the
    day a fourth task type is added and nothing scores it.

    D17: a value nothing is scored against is a value nothing protects.
    """
    from evals.dataset import unfit
    from evals.run_triage_eval import DATASET

    assert unfit(load_jsonl(DATASET)) == []
