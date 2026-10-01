"""build-the-spine ticket 11 — `core.planner`, the plan-shape eval.

The suite checks the wiring and the arithmetic on a scripted model; whether
the Planner's plans are right is the eval's, run by hand (`evals/README.md`).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from friday.kernel.config import AgentConfig
from friday.kernel.evals.planner import (
    CHECKS,
    DATASET,
    Expected,
    Shape,
    build_task,
    load_cases,
    report,
)
from friday.kernel.plugin_host import load_plugins
from friday.kernel.spine.plan import AgentStep, AskStep, DraftStep, HandOverStep, Plan
from friday.kernel.spine.plan_gate import Frozen, full_grant, gate_plan
from friday.sdk.eval import EvalCase
from friday.sdk.testing import ScriptedModel, function_call

REGISTRY = load_plugins(SimpleNamespace(shell_hosts=("local",))).registry


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    from friday.kernel.harness import harness as harness_module

    monkeypatch.setattr(harness_module, "PROVIDER_BACKOFF_SECONDS", 0.0)


def _write(root, name, text, skill=None):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "case.md").write_text(text)
    if skill:
        (folder / "skills" / skill).mkdir(parents=True)
        (folder / "skills" / skill / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: how to trace a purchase\n---\nbody\n"
        )


CASE = """---
action: backend.trace_problem
memory: ["orders-api logs live in Loki"]
expect:
  terminal: draft
  agents: [backend.diagnose]
---
POST /v1/orders returns 500
"""


def test_the_committed_set_is_reachable_under_each_contract():
    """Every case names a registered action, and the plan it expects passes
    GatePlan — else it scores the gate, not the Planner. Every action and
    every terminal step type has a case."""
    cases = load_cases()
    actions, agents = REGISTRY.actions(), REGISTRY.agents()
    assert len(cases) >= 8
    for case in cases:
        verdict = gate_plan(_expected_plan(case, actions[case.inputs.action]), agents)
        assert isinstance(verdict, Frozen), (case.name, verdict)
    assert {c.inputs.action for c in cases} == set(actions)
    assert {c.expected.terminal for c in cases} == {"draft", "ask", "hand_over"}
    assert DATASET.name == "planner"


def _expected_plan(case: EvalCase, action) -> Plan:
    """The smallest plan with the case's expected shape."""
    want = case.expected
    agents = REGISTRY.agents()
    steps: list = [
        AgentStep(
            f"a{i}",
            agent,
            tuple(sorted(full_grant(action.contract, agents[agent]))),
            "b",
        )
        for i, agent in enumerate(want.agents)
    ]
    ids = tuple(s.id for s in steps)
    last = {
        "draft": DraftStep("end", ids),
        "ask": AskStep("end", "q", ids),
        "hand_over": HandOverStep("end", "r", ids),
    }[want.terminal]
    return Plan(0, action.name, 1, None, action.contract, "g", (*steps, last))


def test_a_case_without_its_expectation_is_refused(tmp_path):
    _write(tmp_path, "bad", "---\naction: backend.trace_problem\n---\nhello\n")
    with pytest.raises(ValueError, match="bad: needs"):
        load_cases(tmp_path)


def test_a_case_reads_its_memory_skills_and_expectation(tmp_path):
    _write(tmp_path, "one", CASE, skill="trace-a-purchase")
    [case] = load_cases(tmp_path)
    assert case.inputs.memory == ("orders-api logs live in Loki",)
    assert case.inputs.request == "POST /v1/orders returns 500"
    assert case.expected == Expected("draft", ("backend.diagnose",))


def _graded(expected: Expected, shape: Shape) -> dict[str, bool]:
    case = EvalCase(name="c", inputs=None, expected=expected)
    return {label: check(case, shape) for label, check in CHECKS.items()}


def test_the_checks_grade_terminal_and_agents_and_not_the_grant():
    """Ticket 22: the Planner writes no grant, so the eval grades none."""
    assert set(CHECKS) == {"terminal", "agents"}
    want = Expected("draft", ("backend.diagnose",))
    right = Shape("draft", ("backend.diagnose",))
    assert _graded(want, right) == {"terminal": True, "agents": True}
    asked = Shape("ask")
    assert _graded(want, asked) == {"terminal": False, "agents": False}
    failed = Shape(None, failed="planner_failed: 3 plans refused")
    assert _graded(want, failed) == {"terminal": False, "agents": False}


def test_no_committed_case_expects_a_grant():
    for path in DATASET.glob("*/case.md"):
        assert "toolsets" not in path.read_text(), path.parent.name


def test_the_report_counts_each_check_and_names_every_wrong_case():
    want = Expected("ask")
    results = [
        (EvalCase("ok", None, want), Shape("ask")),
        (EvalCase("off", None, want), Shape("draft", ("backend.diagnose",))),
        (EvalCase("gone", None, want), Shape(None, failed="planner_failed: x")),
    ]
    text = report(results)
    assert "3 cases, whole shape right: 1/3" in text
    assert "terminal: 1/3" in text and "planner_failed: 1/3" in text
    assert "off: got draft via ['backend.diagnose']" in text
    assert "gone: got planner_failed: x" in text


async def test_the_task_runs_the_planner_over_the_case_fixed_memory(tmp_path):
    """The Planner's own memory tool reads what the case seeded, and the
    shape comes back graded."""
    _write(tmp_path, "one", CASE, skill="trace-a-purchase")
    [case] = load_cases(tmp_path)
    model = ScriptedModel(
        [
            [function_call("memory_search", {"query": "Loki"}, call_id="m")],
            [
                function_call(
                    "answer",
                    {
                        "goal": "why orders 500",
                        "steps": [
                            {
                                "id": "s1",
                                "type": "agent",
                                "agent": "backend.diagnose",
                                "brief": "b",
                            },
                            {"id": "s2", "type": "draft", "reads": ["s1"]},
                        ],
                    },
                    call_id="a",
                )
            ],
        ]
    )
    config = SimpleNamespace(
        agent=lambda d: AgentConfig(
            name=d.name,
            api_key="k",
            base_url="https://x.invalid",
            model="m",
            max_turns=4,
        )
    )

    shape = await build_task(config, REGISTRY, model=model)(case)

    assert shape == Shape("draft", ("backend.diagnose",))
    returned = [
        part.content
        for message in model.calls[1].input
        for part in message.parts
        if getattr(part, "tool_name", None) == "memory_search"
        and hasattr(part, "content")
    ]
    assert any("orders-api logs live in Loki" in str(r) for r in returned)
    assert "trace-a-purchase" in str(model.calls[0].input)
