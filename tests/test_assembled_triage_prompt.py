"""The assembled triage prompt (build-the-spine ticket 13; board
`domains-plug-in` ticket 02).

Triage's prompt is the core's reasoning plus every registered action's
`Recognition`, rendered in a stable order, plus examples that add up
(declared → core `skip` → operator-confirmed). The answer schema is closed to
the registered action names plus `skip` and carries no per-label text.
"""

from __future__ import annotations

from friday.kernel.domain.tasks import SKIP
from friday.kernel.domain.triage import make_decided
from friday.kernel.plugin_host import registered_actions
from friday.kernel.triage.prompt import SKIP_EXAMPLES, build_instructions
from friday.sdk import Action, ActionContract, Limits, Recognition


def _registered() -> list[Action]:
    return registered_actions()


def _action(name: str, *, examples=("an example",), not_when=()) -> Action:
    return Action(
        name=name,
        recognition=Recognition(
            means=f"what {name} means",
            pick_when=(f"a sign of {name}",),
            not_when=not_when,
            examples=examples,
        ),
        contract=ActionContract(
            allowed_step_types=frozenset(),
            allowed_agents=frozenset(),
            allowed_toolsets=frozenset(),
            constraints=(),
            approval_policy="",
            acceptance_template="",
            limits=Limits(max_replans=0, max_steps=1),
        ),
    )


def test_the_three_actions_are_registered_with_their_recognition():
    names = {a.name for a in _registered()}
    assert names == {
        "backend.trace_problem",
        "backend.answer_question",
        "ops.request_permission",
    }


def test_labels_render_sorted_by_name_with_skip_last():
    b = _action("b.second", examples=("b says",))
    a = _action(
        "a.first", examples=("a says",), not_when=(("a sign of b", "b.second"),)
    )
    built = build_instructions(actions=[b, a])

    labels = built.split("<labels>")[1].split("</labels>")[0]
    assert labels.index("a.first") < labels.index("b.second") < labels.index(SKIP)
    assert "what a.first means" in labels and "a sign of a.first" in labels
    assert "a sign of b → b.second" in labels


def test_the_core_reasoning_puts_no_label_first():
    built = build_instructions(actions=_registered())
    assert "no label comes first" in built
    assert "in this order" not in built
    # No catch-all: the core never names a plugin's action.
    for action in _registered():
        thinking = built.split("<thinking_style>")[1].split("</thinking_style>")[0]
        assert action.name not in thinking


def test_examples_add_up_declared_then_skip_then_confirmed():
    a = _action("a.first", examples=("a says",))
    built = build_instructions(
        actions=[a], examples=[("the operator marked this", "a.first")]
    )

    examples = built.split("<examples>")[1].split("</examples>")[0]
    order = ["a says", *SKIP_EXAMPLES, "the operator marked this"]
    positions = [examples.index(text) for text in order]
    assert positions == sorted(positions)


def test_a_confirmed_row_already_declared_is_shown_once():
    a = _action("a.first", examples=("a says",))
    built = build_instructions(actions=[a], examples=[("a says", "a.first")])
    assert built.count("a says") == 1


def test_rendered_prompt_bytes_are_stable_across_runs_and_registration_order():
    actions = _registered()
    once = build_instructions(actions=actions, examples=[("x", SKIP)])
    again = build_instructions(actions=list(reversed(actions)), examples=[("x", SKIP)])
    assert once.encode() == again.encode()


def test_the_answer_schema_is_closed_to_registered_actions_plus_skip():
    from friday.kernel.harness.model_client import _answer_params

    names = [a.name for a in _registered()]
    described = _answer_params(make_decided(names))["properties"]["type"]

    assert described["enum"] == [*sorted(names), SKIP]
    # Label meaning lives in the prompt only.
    assert "labels above" in described["description"]
    for action in _registered():
        assert action.recognition.means not in described["description"]


def test_the_schema_is_the_same_class_for_the_same_set_in_any_order():
    assert make_decided(["b", "a"]) is make_decided(["a", "b"])


def test_no_declared_example_is_also_in_the_triage_eval():
    """Scoring a classifier on a sentence it was told the answer to is not a
    measurement (board `domains-plug-in` ticket 02 §5: a suite test, not a boot
    check)."""
    import pytest

    from friday.kernel.evals.cases import load_turn_cases
    from friday.kernel.evals.triage import DATASET

    if not DATASET.is_dir():
        pytest.skip("evals/datasets/triage/ is not on this machine (gitignored)")
    scored = {t.strip() for case in load_turn_cases(DATASET) for t, _ in case.inputs}
    declared = {t.strip() for a in _registered() for t in a.recognition.examples}
    declared |= {t.strip() for t in SKIP_EXAMPLES}
    assert not declared & scored
