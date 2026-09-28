"""Ticket 06 — the runner that scores the live classifier.

Driven with `ScriptedModel`, same as `tests/test_triage.py`: this is not
where the classifier's judgement is checked, that only a real run against
`evals/triage.jsonl` can answer, and it costs money — see `evals/README.md`.
What is under test here is the wiring: that a real `Triage`, given a real
`Database`, turns each dataset row into the right `Prediction`.
"""

from __future__ import annotations

from friday.sdk.testing import ScriptedModel, function_call

from evals.dataset import Example
from evals.run_triage_eval import _to_prediction, report, run
from evals.scoring import Prediction
from friday.kernel.config import AgentConfig
from friday.kernel.domain.triage import Decided, NeedsHuman

CONFIG = AgentConfig(
    name="triage",
    api_key="k",
    base_url="https://example.invalid/v1",
    model="test-model",
)


def test_a_decided_outcome_becomes_a_prediction_carrying_its_confidence():
    example = Example(text="the api is down", expected="devops.api_issue")

    prediction = _to_prediction(example, Decided(type="devops.api_issue", confidence=0.9))

    assert prediction == Prediction(
        expected="devops.api_issue", predicted="devops.api_issue", confidence=0.9
    )


def test_a_needs_human_outcome_becomes_a_prediction_saying_so():
    """Not a task type it guessed and got wrong — the actual outcome the row
    produced, at zero confidence, matching `TriageRunner._record`'s own
    mapping for the same case."""
    example = Example(text="???", expected="devops.api_issue")

    prediction = _to_prediction(example, NeedsHuman("triage produced no classification"))

    assert prediction == Prediction(
        expected="devops.api_issue", predicted="needs_human", confidence=0.0
    )


async def test_run_scores_every_row_in_the_dataset(db, tmp_path):
    from evals.dataset import write_jsonl

    dataset = tmp_path / "triage.jsonl"
    write_jsonl(
        dataset,
        [
            Example(text="the api is down", expected="devops.api_issue"),
            Example(text="anyone want lunch", expected="skip"),
        ],
    )

    triage = _scripted_triage(
        [function_call("answer", {"type": "devops.api_issue", "confidence": 0.9}, call_id="1")],
        [function_call("answer", {"type": "skip", "confidence": 0.4}, call_id="1")],
    )

    predictions = await run(dataset_path=dataset, triage=triage)

    assert predictions == [
        Prediction(expected="devops.api_issue", predicted="devops.api_issue", confidence=0.9),
        Prediction(expected="skip", predicted="skip", confidence=0.4),
    ]


def test_report_names_the_dataset_size_and_the_accuracy():
    text = report(
        [
            Prediction(expected="skip", predicted="skip", confidence=0.9),
            Prediction(expected="devops.api_issue", predicted="skip", confidence=0.5),
        ]
    )

    assert "2 examples" in text
    assert "50.0%" in text


def test_report_on_an_empty_run_does_not_crash():
    """An eval set nobody has built yet, or a dataset path pointed at
    nothing, should say so as a report — not `max()` on an empty sequence."""
    text = report([])

    assert "0 examples" in text
    assert "0.0%" in text


def test_report_lays_out_three_or_more_predicted_labels():
    """`width = max(16, *(...))` takes a `*args` unpacking of the label
    widths — with three or more labels that is `max(16, a, b, c)`, not the
    two-argument form a shorter dataset happens to exercise."""
    text = report(
        [
            Prediction(expected="devops.api_issue", predicted="devops.api_issue", confidence=0.9),
            Prediction(expected="access_request", predicted="access_request", confidence=0.9),
            Prediction(expected="doc_question", predicted="doc_question", confidence=0.9),
            Prediction(expected="skip", predicted="needs_human", confidence=0.0),
        ]
    )

    for label in ("devops.api_issue", "access_request", "doc_question", "skip", "needs_human"):
        assert label in text


def _scripted_triage(*steps):
    from friday.kernel.triage import Triage

    return Triage(config=CONFIG, model=ScriptedModel(list(steps)))


async def test_a_multi_message_row_reaches_triage_as_a_real_turn(tmp_path):
    """The whole reason ticket 09 extended the dataset format: a row with a
    `turn` must actually reach `Triage.decide` as multiple raw messages, not
    a joined string — otherwise nothing here ever exercises the ownership
    mark or a real multi-line render, which is exactly the gap ticket 09's
    own criterion named."""

    from evals.dataset import write_jsonl
    from friday.kernel.triage import Triage

    dataset = tmp_path / "triage.jsonl"
    write_jsonl(
        dataset,
        [
            Example(
                text="whatever — a turn is given instead",
                expected="devops.api_issue",
                turn=(
                    ("api lỗi rồi anh ơi", False),
                    ("correlationId nằm trong x-request-id đó em", True),
                ),
            ),
        ],
    )

    seen_turns: list = []

    answering = ScriptedModel(
        [[function_call("answer", {"type": "devops.api_issue", "confidence": 0.9})]]
    )

    class _RecordingTriage(Triage):
        async def decide(self, event, *, turn=()):
            seen_turns.append(turn)
            return await super().decide(event, turn=turn)

    triage = _RecordingTriage(config=CONFIG, model=answering)

    await run(dataset_path=dataset, triage=triage)

    (turn,) = seen_turns
    assert [m.text for m in turn] == [
        "api lỗi rồi anh ơi", "correlationId nằm trong x-request-id đó em",
    ]
    assert [m.is_own for m in turn] == [False, True]


async def test_an_invented_type_is_reported_as_its_own_number(tmp_path):
    """D20, end to end through the runner: a model that names a type that does
    not exist is scored as `needs_human` — the outcome the row produced — and
    counted separately beside it.

    Driven through a real `Triage` on a scripted model, so what is exercised is
    the validation that actually refuses the type rather than a flag a test set
    by hand.
    """
    from friday.sdk.testing import ScriptedModel, function_call

    from evals.dataset import Example, write_jsonl
    from evals.run_triage_eval import report, run
    from evals.scoring import out_of_set
    from friday.kernel.triage import Triage

    dataset = tmp_path / "triage.jsonl"
    write_jsonl(dataset, [Example(text="the api is 500ing", expected="devops.api_issue")])

    invented = {"type": "hardware_issue", "confidence": 0.9}
    triage = Triage(
        config=CONFIG,
        model=ScriptedModel([
            [function_call("answer", invented, call_id="1")],
            [function_call("answer", invented, call_id="2")],
        ]),
    )

    predictions = await run(dataset, triage=triage)

    assert out_of_set(predictions) == 1
    assert predictions[0].predicted == "needs_human", (
        "the mention still reaches a person, which is what the row produced"
    )
    assert "decisions outside the closed set: 1" in report(predictions)


async def test_a_clean_run_still_reports_the_number_as_zero(tmp_path):
    """A line that appears only when it is non-zero is a line whose absence
    means both "none" and "not measured"."""
    from friday.sdk.testing import ScriptedModel, function_call

    from evals.dataset import Example, write_jsonl
    from evals.run_triage_eval import report, run
    from friday.kernel.triage import Triage

    dataset = tmp_path / "triage.jsonl"
    write_jsonl(dataset, [Example(text="the api is 500ing", expected="devops.api_issue")])

    triage = Triage(
        config=CONFIG,
        model=ScriptedModel([
            [function_call("answer", {"type": "devops.api_issue", "confidence": 0.9}, call_id="1")]
        ]),
    )

    assert "decisions outside the closed set: 0" in report(
        await run(dataset, triage=triage)
    )


# --- the fail-loud guard on the pydantic-evals runner (ticket 18) ------------


class _FakeCase:
    def __init__(self, output):
        self.output = output


class _FakeFailure:
    name = "eval-3"
    error_message = "provider down"


class _FakeReport:
    def __init__(self, cases, failures):
        self.cases = cases
        self.failures = failures


def test_a_dropped_eval_row_is_an_error_not_a_quietly_smaller_set():
    """`pydantic-evals` parks a row whose task raised in `report.failures` and
    returns only the successes. For a scored regression net that is the wrong
    default — accuracy and the confusion matrix would be computed over a smaller
    denominator with nothing saying so — so the runner raises instead. Deleting
    the `report.failures` check turns a transient provider error into a
    better-looking score, which is exactly the silence this guards."""
    import pytest

    from evals import outputs_or_raise

    with pytest.raises(RuntimeError, match="smaller than the set given"):
        outputs_or_raise(_FakeReport([_FakeCase("a")], [_FakeFailure()]))


def test_when_every_row_ran_their_outputs_come_back_in_order():
    from evals import outputs_or_raise

    assert outputs_or_raise(
        _FakeReport([_FakeCase("a"), _FakeCase("b")], [])
    ) == ["a", "b"]
