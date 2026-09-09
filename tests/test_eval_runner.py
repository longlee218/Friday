"""Ticket 06 — the runner that scores the live classifier.

Driven with `ScriptedModel`, same as `tests/test_triage.py`: this is not
where the classifier's judgement is checked, that only a real run against
`evals/triage.jsonl` can answer, and it costs money — see `evals/README.md`.
What is under test here is the wiring: that a real `Triage`, given a real
`Database`, turns each dataset row into the right `Prediction`.
"""

from __future__ import annotations

from agents.testing import ScriptedModel, function_call

from evals.dataset import Example
from evals.run_triage_eval import _to_prediction, report, run
from evals.scoring import Prediction
from friday.config import AgentConfig
from friday.domain.actions import Decided, NeedsHuman

CONFIG = AgentConfig(
    name="triage",
    api_key="k",
    base_url="https://example.invalid/v1",
    model="test-model",
)


def test_a_decided_outcome_becomes_a_prediction_carrying_its_confidence():
    example = Example(text="the api is down", expected="api_issue")

    prediction = _to_prediction(example, Decided(type="api_issue", confidence=0.9))

    assert prediction == Prediction(
        expected="api_issue", predicted="api_issue", confidence=0.9
    )


def test_a_needs_human_outcome_becomes_a_prediction_saying_so():
    """Not a task type it guessed and got wrong — the actual outcome the row
    produced, at zero confidence, matching `TriageRunner._record`'s own
    mapping for the same case."""
    example = Example(text="???", expected="api_issue")

    prediction = _to_prediction(example, NeedsHuman("triage produced no classification"))

    assert prediction == Prediction(
        expected="api_issue", predicted="needs_human", confidence=0.0
    )


async def test_run_scores_every_row_in_the_dataset(db, tmp_path):
    from evals.dataset import write_jsonl

    dataset = tmp_path / "triage.jsonl"
    write_jsonl(
        dataset,
        [
            Example(text="the api is down", expected="api_issue"),
            Example(text="anyone want lunch", expected="skip"),
        ],
    )

    triage = _scripted_triage(
        [function_call("classify", {"task_type": "api_issue", "confidence": 0.9}, call_id="1")],
        [function_call("skip", {"confidence": 0.4}, call_id="1")],
    )

    predictions = await run(dataset_path=dataset, triage=triage)

    assert predictions == [
        Prediction(expected="api_issue", predicted="api_issue", confidence=0.9),
        Prediction(expected="skip", predicted="skip", confidence=0.4),
    ]


def test_report_names_the_dataset_size_and_the_accuracy():
    text = report(
        [
            Prediction(expected="skip", predicted="skip", confidence=0.9),
            Prediction(expected="api_issue", predicted="skip", confidence=0.5),
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
            Prediction(expected="api_issue", predicted="api_issue", confidence=0.9),
            Prediction(expected="access_request", predicted="access_request", confidence=0.9),
            Prediction(expected="doc_question", predicted="doc_question", confidence=0.9),
            Prediction(expected="skip", predicted="needs_human", confidence=0.0),
        ]
    )

    for label in ("api_issue", "access_request", "doc_question", "skip", "needs_human"):
        assert label in text


def _scripted_triage(*steps):
    from friday.triage import Triage

    return Triage(config=CONFIG, model=ScriptedModel(list(steps)))


async def test_a_multi_message_row_reaches_triage_as_a_real_turn(tmp_path):
    """The whole reason ticket 09 extended the dataset format: a row with a
    `turn` must actually reach `Triage.decide` as multiple raw messages, not
    a joined string — otherwise nothing here ever exercises the ownership
    mark or a real multi-line render, which is exactly the gap ticket 09's
    own criterion named."""
    from agents.models.interface import Model

    from evals.dataset import write_jsonl
    from friday.triage import Triage

    dataset = tmp_path / "triage.jsonl"
    write_jsonl(
        dataset,
        [
            Example(
                text="whatever — a turn is given instead",
                expected="api_issue",
                turn=(
                    ("api lỗi rồi anh ơi", False),
                    ("correlationId nằm trong x-request-id đó em", True),
                ),
            ),
        ],
    )

    seen_turns: list = []

    class _Capturing(Model):
        async def get_response(self, *a, **kw):
            return await ScriptedModel([
                function_call("classify", {"task_type": "api_issue", "confidence": 0.9}, call_id="1")
            ]).get_response(*a, **kw)

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    class _RecordingTriage(Triage):
        async def decide(self, event, *, turn=()):
            seen_turns.append(turn)
            return await super().decide(event, turn=turn)

    triage = _RecordingTriage(config=CONFIG, model=_Capturing())

    await run(dataset_path=dataset, triage=triage)

    (turn,) = seen_turns
    assert [m.text for m in turn] == [
        "api lỗi rồi anh ơi", "correlationId nằm trong x-request-id đó em",
    ]
    assert [m.is_own for m in turn] == [False, True]
