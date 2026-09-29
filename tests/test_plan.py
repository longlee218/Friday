"""The plan: its shape rules, its hash, and the step key results are stored by.

Build-the-spine ticket 06 (decisions: `domains-plug-in` tickets 10 and 14 §5).
Each shape test breaks one rule on a plan that is otherwise clean, so it proves
its own rule and nothing else.
"""

from __future__ import annotations

from dataclasses import replace

from friday.kernel.spine.plan import (
    AgentStep,
    AskStep,
    DraftStep,
    HandOverStep,
    Plan,
    plan_hash,
    shape_errors,
    step_keys,
)
from friday.sdk import ActionContract, Limits

CONTRACT = ActionContract(
    allowed_step_types=frozenset({"agent", "ask", "hand_over", "draft"}),
    allowed_agents=frozenset({"demo.diagnose"}),
    allowed_toolsets=frozenset({"demo.logs", "demo.code", "core.memory"}),
    constraints=("every ref points at a line that was read",),
    approval_policy="Reply",
    acceptance_template="grounded; weighed",
    limits=Limits(max_replans=2, max_steps=3),
)

P1 = AgentStep(
    "p1",
    "demo.diagnose",
    ("demo.logs", "demo.code"),
    brief="find the 400 for the correlationId",
)
PLACE = ("prod", "onboarding", "/srv/onboarding", "acme/onboarding", "v1.4.2")


def _plan(*steps, version: int = 1, contract: ActionContract = CONTRACT) -> Plan:
    return Plan(
        task_id=412,
        action="demo.trace",
        plan_version=version,
        replaces=None,
        contract=contract,
        goal="why the 400",
        steps=steps,
    )


V1 = _plan(P1, DraftStep("p2", reads=("p1",)))


# ── shape ────────────────────────────────────────────────────────────────────
def test_a_clean_plan_has_no_shape_errors():
    assert shape_errors(V1) == []
    assert shape_errors(_plan(AskStep("a", "which env?"))) == []
    assert (
        shape_errors(_plan(P1, HandOverStep("h", "out of reach", reads=("p1",)))) == []
    )


def test_duplicate_step_ids_are_refused():
    errors = shape_errors(_plan(P1, DraftStep("p1", reads=())))
    assert any("duplicate step id p1" in e for e in errors)


def test_a_terminal_step_before_the_last_is_refused():
    errors = shape_errors(
        _plan(AskStep("a", "which env?"), P1, DraftStep("d", reads=("p1",)))
    )
    assert errors == ["a: ask only as the last step"]


def test_a_plan_that_does_not_end_on_a_terminal_step_is_refused():
    assert shape_errors(_plan(P1)) == ["the last step must be draft | ask | hand_over"]


def test_an_empty_plan_is_refused():
    assert shape_errors(_plan()) == ["the plan has no steps"]


def test_reads_of_a_later_step_are_refused():
    later = replace(P1, id="p0", reads=("p1",))
    errors = shape_errors(
        _plan(later, replace(P1, brief="other"), DraftStep("d", reads=("p0",)))
    )
    assert errors == ["p0: reads p1, not an earlier step"]


def test_reads_of_an_unknown_step_are_refused():
    errors = shape_errors(_plan(P1, DraftStep("d", reads=("p9",))))
    assert errors == ["d: reads p9, not an earlier step"]


def test_a_draft_that_reads_nothing_is_refused():
    errors = shape_errors(_plan(P1, DraftStep("d", reads=())))
    assert errors == ["d: a draft must read at least one step"]


def test_something_that_is_not_a_step_is_refused():
    errors = shape_errors(_plan("p1", DraftStep("d", reads=("p1",))))
    assert errors == ["'p1' is not a step (agent | ask | hand_over | draft)"]


# ── plan hash ────────────────────────────────────────────────────────────────
def test_plan_hash_is_pinned_across_processes():
    """Pinned to a literal: the contract holds frozensets, whose order changes
    with PYTHONHASHSEED, so an unsorted canonical form would move this."""
    assert plan_hash(V1) == (
        "0fab4488d57d4ae8abb77b576db116e6d35981deb66ce2b9b3177c05c3f7c7fb"
    )


def test_plan_hash_covers_the_contract_and_the_version():
    wider = replace(CONTRACT, allowed_toolsets=CONTRACT.allowed_toolsets | {"demo.db"})
    assert plan_hash(replace(V1, contract=wider)) != plan_hash(V1)
    assert plan_hash(replace(V1, plan_version=2)) != plan_hash(V1)


# ── step key ─────────────────────────────────────────────────────────────────
def test_same_step_content_keeps_its_key_across_versions():
    v2 = _plan(
        P1,
        AgentStep(
            "p3", "demo.diagnose", ("demo.logs",), "follow upstream", reads=("p1",)
        ),
        DraftStep("p4", reads=("p1", "p3")),
        version=2,
    )
    k1, k2 = step_keys(V1, PLACE), step_keys(v2, PLACE)
    assert k1["p1"] == k2["p1"]
    assert k1["p2"] != k2["p4"]  # a draft that reads more is a different step


def test_the_step_id_is_not_in_the_key():
    renamed = _plan(replace(P1, id="first"), DraftStep("last", reads=("first",)))
    assert list(step_keys(renamed, PLACE).values()) == list(
        step_keys(V1, PLACE).values()
    )


def test_a_changed_step_changes_its_key_and_every_step_reading_it():
    edited = _plan(
        replace(P1, brief=P1.brief + ", check headers too"),
        DraftStep("p2", reads=("p1",)),
    )
    before, after = step_keys(V1, PLACE), step_keys(edited, PLACE)
    assert before["p1"] != after["p1"]
    assert before["p2"] != after["p2"]


def test_a_changed_placement_changes_every_key():
    moved = ("staging",) + PLACE[1:]
    before, after = step_keys(V1, PLACE), step_keys(V1, moved)
    assert all(before[s] != after[s] for s in before)


def test_an_empty_placement_is_a_placement_like_any_other():
    """`ops` has no enricher: its identity is `()` and never changes."""
    assert step_keys(V1, ()) == step_keys(V1, ())
    assert step_keys(V1, ()) != step_keys(V1, PLACE)


def test_step_fields_of_the_wrong_type_are_refused():
    """A bare string where a tuple belongs would be iterated a letter at a time."""
    errors = shape_errors(
        _plan(replace(P1, toolsets="demo.logs"), DraftStep("d", reads="p1"))
    )
    assert errors == [
        "'p1': toolsets must be a tuple of strings",
        "'d': reads must be a tuple of strings",
    ]
    assert shape_errors(_plan(AskStep("a", question=None))) == [
        "'a': question must be a string"
    ]
