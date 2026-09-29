"""`core.triage` and the core's eval runner, on scripted models.

Over the 200-line target: it covers the runner, the triage task, the report,
the set's fitness guard and the one-importer guard, which share fixtures.

What the classifier judges is only a live run's to say (`uv run run_eval.py
core.triage`, which costs money). What is checked here is the wiring: a case
on disk reaches a real `Triage` as a real turn, each outcome becomes the right
`Prediction`, a dropped case is an error, and the local set is fit to
score against.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from friday.kernel.config import AgentConfig
from friday.kernel.domain.triage import Decided, NeedsHuman
from friday.kernel.evals.cases import load_turn_cases
from friday.kernel.evals.run import outputs_or_raise, run
from friday.kernel.evals.triage import (
    DATASET,
    TRIAGE_EVAL,
    build_task,
    decisions,
    report,
    to_prediction,
    unfit,
)
from friday.kernel.evals.triage_scoring import Prediction
from friday.kernel.plugin_host import registered_actions
from friday.sdk import EvalCase
from friday.sdk.testing import ScriptedModel, function_call

REPO = Path(__file__).resolve().parent.parent
CONFIG = AgentConfig(
    name="triage", api_key="k", base_url="https://example.invalid/v1", model="m"
)


def _case(text="the api is down", expected="backend.trace_problem", name="c"):
    return EvalCase(name=name, inputs=((text, False),), expected=expected)


def _spec_over(root):
    return replace(TRIAGE_EVAL, cases=lambda: load_turn_cases(root))


def _write(root, rel, content):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _triage(*steps):
    from friday.kernel.triage import Triage

    return Triage(config=CONFIG, model=ScriptedModel(list(steps)))


# --- outcome to prediction ---------------------------------------------------


def test_a_decided_outcome_becomes_a_prediction_carrying_its_confidence():
    got = to_prediction(_case(), Decided(type="backend.trace_problem", confidence=0.9))

    assert got == Prediction("backend.trace_problem", "backend.trace_problem", 0.9)


def test_a_needs_human_outcome_becomes_a_prediction_saying_so():
    got = to_prediction(_case(), NeedsHuman("no classification"))

    assert got == Prediction("backend.trace_problem", "needs_human", 0.0)


# --- end to end through the core runner ------------------------------------


async def test_every_case_on_disk_is_scored_in_order(tmp_path):
    _write(
        tmp_path,
        "backend.trace_problem/001-a.md",
        "---\nexpected_task: backend.trace_problem\n---\nthe api is down\n",
    )
    _write(
        tmp_path, "skip/001-b.md", "---\nexpected_task: skip\n---\nanyone want lunch\n"
    )
    triage = _triage(
        [
            function_call(
                "answer",
                {"type": "backend.trace_problem", "confidence": 0.9},
                call_id="1",
            )
        ],
        [function_call("answer", {"type": "skip", "confidence": 0.4}, call_id="1")],
    )

    ran = await run(_spec_over(tmp_path), build_task(triage))

    assert [(c.name, p) for c, p in ran.results] == [
        (
            "backend.trace_problem/001-a",
            Prediction("backend.trace_problem", "backend.trace_problem", 0.9),
        ),
        ("skip/001-b", Prediction("skip", "skip", 0.4)),
    ]
    # The framework's own per-case table: a bool check is its assertion column.
    assert "skip/001-b" in ran.table and "Assertions" in ran.table and "✔" in ran.table


async def test_two_cases_with_one_name_are_refused():
    from dataclasses import replace as with_

    twice = with_(TRIAGE_EVAL, cases=lambda: [_case(name="x"), _case(name="x")])

    with pytest.raises(ValueError, match="more than one case is named x"):
        await run(twice, build_task(_triage()))


async def test_a_turn_reaches_triage_as_its_raw_messages(tmp_path):
    """A multi-message case must reach `Triage.decide` as a real turn — the
    ownership mark and the multi-line render are what a joined string hides."""
    from friday.kernel.triage import Triage

    _write(
        tmp_path,
        "backend.trace_problem/001-t.md",
        (
            "---\nexpected_task: backend.trace_problem\nturn:\n"
            '  - text: "api lỗi rồi anh ơi"\n'
            '  - text: "correlationId nằm trong x-request-id đó em"\n    own: true\n---\n'
        ),
    )
    seen = []

    class _Recording(Triage):
        async def decide(self, event, *, turn=()):
            seen.append(turn)
            return await super().decide(event, turn=turn)

    triage = _Recording(
        config=CONFIG,
        model=ScriptedModel(
            [
                [
                    function_call(
                        "answer", {"type": "backend.trace_problem", "confidence": 0.9}
                    )
                ]
            ]
        ),
    )

    await run(_spec_over(tmp_path), build_task(triage))

    (turn,) = seen
    assert [(m.text, m.is_own) for m in turn] == [
        ("api lỗi rồi anh ơi", False),
        ("correlationId nằm trong x-request-id đó em", True),
    ]


async def test_an_invented_type_is_its_own_number(tmp_path):
    """D20: a type that does not exist is `needs_human` — what the case
    produced — and counted apart from an outage."""
    _write(
        tmp_path,
        "backend.trace_problem/001-a.md",
        "---\nexpected_task: backend.trace_problem\n---\nthe api is 500ing\n",
    )
    invented = {"type": "hardware_issue", "confidence": 0.9}
    triage = _triage(
        [function_call("answer", invented, call_id="1")],
        [function_call("answer", invented, call_id="2")],
    )

    results = (await run(_spec_over(tmp_path), build_task(triage))).results

    assert results[0][1].predicted == "needs_human" and results[0][1].out_of_set
    assert "decisions outside the closed set: 1/1" in report(results)


# --- the report --------------------------------------------------------------


def test_the_report_names_size_accuracy_and_every_wrong_case():
    text = report(
        [
            (_case(name="skip/001", expected="skip"), Prediction("skip", "skip", 0.9)),
            (
                _case(name="backend.trace_problem/007"),
                Prediction("backend.trace_problem", "needs_human", 0.0),
            ),
        ]
    )

    assert "2 examples, accuracy 50.0%" in text
    assert "decisions outside the closed set: 0/2" in text, (
        "zero is printed, not omitted"
    )
    assert "wrong: 1/2" in text
    assert "backend.trace_problem/007: predicted needs_human (0.00)" in text


def test_the_report_on_no_cases_does_not_crash():
    assert "0 examples" in report([])


# --- a dropped case is an error ----------------------------------------------


class _Report:
    def __init__(self, outputs, failures=()):
        self.cases = [type("C", (), {"output": o})() for o in outputs]
        self.failures = list(failures)


def test_a_dropped_case_is_an_error_not_a_quietly_smaller_set():
    failure = type("F", (), {"name": "eval-3", "error_message": "provider down"})()

    with pytest.raises(RuntimeError, match="smaller than the set given"):
        outputs_or_raise(_Report(["a"], [failure]))


def test_when_every_case_ran_their_outputs_come_back_in_order():
    assert outputs_or_raise(_Report(["a", "b"])) == ["a", "b"]


# --- the set has to be fit to score against -----------------------------------


DECISIONS = (
    "backend.trace_problem",
    "backend.answer_question",
    "ops.request_permission",
    "skip",
)


def test_a_set_missing_a_decision_says_which_one():
    said = unfit([_case()], DECISIONS)

    assert any("'skip'" in line for line in said)


def test_a_set_with_no_multi_message_turn_says_so():
    every = [_case(text=d, expected=d, name=d) for d in DECISIONS]

    assert any("turn" in line for line in unfit(every, DECISIONS))


def test_a_repeated_turn_is_reported_by_both_names():
    twice = [_case(name="a"), _case(name="b")]

    assert any("b repeats a" in line for line in unfit(twice, DECISIONS))


def test_the_set_this_repo_ships_is_fit_to_score_against():
    """The guard over the set itself: nothing else notices the day every
    `skip` case is deleted or a new action has no case. The set is not in git
    (real traffic, with tokens and customer data), so a clone without it
    skips."""
    if not DATASET.is_dir():
        pytest.skip("evals/datasets/triage/ is not on this machine (gitignored)")
    assert unfit(load_turn_cases(DATASET), decisions(registered_actions())) == []


# --- one module imports the library ------------------------------------------


def test_only_one_module_imports_pydantic_evals():
    """One module per adopted library: replacing it stays a rewrite of one
    file only while this holds."""
    importers = set()
    for path in REPO.rglob("*.py"):
        relative = path.relative_to(REPO).as_posix()
        if (
            relative.startswith((".venv/", "web/", ".agents/"))
            or "/node_modules/" in relative
        ):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(n.split(".")[0] == "pydantic_evals" for n in names):
                importers.add(relative)
    assert importers == {"friday/kernel/evals/run.py"}
