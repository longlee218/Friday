"""`backend.diagnose`: the agent `backend.trace_problem` grants.

Declared here so the action's contract names a registered agent (boot refusal
4). It reuses the DAG node's instructions and `Diagnosis` until build-the-spine
ticket 14 runs it on the spine and moves both here. Tier and tokens are the DAG
node's (`plugins/backend/graph`); `max_turns` is ticket 06's number for
`backend.explain` — a starting value, not measured.
"""

from __future__ import annotations

from friday.sdk import AgentSpec, Budget
from plugins.backend.graph.diagnose import Diagnosis
from plugins.backend.graph.prompt import build_instructions

__all__ = ["DIAGNOSE"]

DIAGNOSE = AgentSpec(
    name="backend.diagnose",
    description="Finds what caused a failing request: reads the service's logs "
    "and its code at the running tag, and names the line that rejects it.",
    instructions=build_instructions(reads=True),
    result=Diagnosis,
    tier="flash",
    toolsets=("backend.logs", "backend.code", "core.memory", "core.skills"),
    budget=Budget(max_turns=20, tokens=500_000),
    temperature=0.0,
)
