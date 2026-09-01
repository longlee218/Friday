"""TEMPORARY QA PROBE — delete before finishing."""
from __future__ import annotations

from types import SimpleNamespace

from friday.dag import DAGDeps, DAGRunner
from friday.dag.api_issue import build_api_issue_dag
from friday.dag.pause import PauseForHuman


class StubAgent:
    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    async def run(self, prompt, **kw):
        self.prompts.append(prompt)
        return SimpleNamespace(final_output=self.answer) if self.answer is not None else None


def deps(agents, servers, **params):
    task = SimpleNamespace(id=1, params={
        "summary": "checkout is 500", "environment": "production",
        "correlation_id": "abcdef01-2345-6789-abcd-ef0123456789", "curl": None, **params})
    return DAGDeps(task=task, servers=servers, extra=agents)


async def test_probe_null_cause_but_actionable_true_reaches_fix_bug():
    """The model returns actionable:true with cause:null. Does _fix_bug run,
    and does the _HANDS_OFF guard still protect anything?"""
    fixer = StubAgent("--- a/x.py\n+++ b/x.py\n-1\n+2")
    agents = {
        "read_logs": StubAgent("ERROR at migrations/versions/abc.py:12"),
        "find_code_path": StubAgent("migrations/versions/abc.py:12"),
        "analyze_stack": StubAgent('{"cause": null, "actionable": true}'),
        "fix_bug": fixer,
        "compose_reply": StubAgent("all done"),
    }
    servers = {"loki": object(), "source": object()}
    runner = DAGRunner(build_api_issue_dag(), deps=deps(agents, servers))
    state = await runner.run()
    print("\nPROBE trail:", runner.trail)
    print("PROBE fixer was asked:", fixer.prompts)
    print("PROBE fix_bug result:", state.get("fix_bug"))
    print("PROBE final action:", state.get("compose_reply"))


async def test_probe_hands_off_ignores_the_file_path():
    """Cause is innocuous prose; the code located is a migration. Guard?"""
    fixer = StubAgent("patched")
    agents = {
        "read_logs": StubAgent("ERROR"),
        "find_code_path": StubAgent("migrations/versions/443468757024_baseline_schema.py:20"),
        "analyze_stack": StubAgent('{"cause": "off-by-one in the loop bound", "actionable": true}'),
        "fix_bug": fixer,
        "compose_reply": StubAgent("done"),
    }
    servers = {"loki": object(), "source": object()}
    runner = DAGRunner(build_api_issue_dag(), deps=deps(agents, servers))
    state = await runner.run()
    print("\nPROBE trail:", runner.trail)
    print("PROBE fixer prompts:", fixer.prompts)
    print("PROBE action:", state.get("compose_reply"))


async def test_probe_fix_result_lost_when_cause_is_null():
    """fix_bug edited code; compose_reply has cause=None. What is sent?"""
    agents = {
        "read_logs": StubAgent("ERROR"),
        "find_code_path": StubAgent("app/x.py:9"),
        "analyze_stack": StubAgent('{"cause": null, "actionable": true}'),
        "fix_bug": StubAgent("diff --git a/app/x.py"),
        "compose_reply": StubAgent("written"),
    }
    servers = {"loki": object(), "source": object()}
    runner = DAGRunner(build_api_issue_dag(), deps=deps(agents, servers))
    state = await runner.run()
    print("\nPROBE state:", dict(state.results))
    print("PROBE action sent to the reporter:", state.get("compose_reply"))
