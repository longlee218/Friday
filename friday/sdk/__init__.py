"""Friday's SDK surface: the contracts a plugin registers against.

Protocols and dataclasses only — no I/O, no third-party imports. What the kernel
builds on and what a plugin codes against, so a plugin never reaches into the
core. The workflow port (`workflow.py`) landed in ticket 06; the plugin and
memory contracts (`plugin.py`, `memory.py`) in ticket 10; the test-double seam
(`testing/`) in ticket 05.

The dependency rule (`tests/test_dependency_rule.py`) holds this in place: `sdk`
is the bottom of our own code — it imports nothing of ours — `kernel` imports
`sdk`, and a plugin imports `sdk` only.
"""

from friday.sdk.action import Action, ActionContract, Limits, Recognition
from friday.sdk.agent import AgentSpec, Budget
from friday.sdk.eval import EvalCase, EvalSpec
from friday.sdk.evidence import Evidence
from friday.sdk.intake import ArtifactRef, Hints, IntakeContext, IntakeSeed
from friday.sdk.memory import MemoryKindSpec, Origin
from friday.sdk.model import Model
from friday.sdk.outbox import Kind
from friday.sdk.plugin import Plugin, PluginAPI, TaskTypeSpec
from friday.sdk.redact import scrub
from friday.sdk.sources import CodeSource, Lines, LogSource, Reads
from friday.sdk.toolset import RunContext, ToolContext, ToolsetSpec, ToolSpec, tool
from friday.sdk.validation import (
    InSet,
    Matches,
    NonEmpty,
    OneOf,
    Problem,
    asked_as,
    validate,
)

__all__ = [
    "Action",
    "ActionContract",
    "AgentSpec",
    "ArtifactRef",
    "Budget",
    "CodeSource",
    "EvalCase",
    "EvalSpec",
    "Evidence",
    "Hints",
    "InSet",
    "IntakeContext",
    "IntakeSeed",
    "Kind",
    "Limits",
    "Lines",
    "LogSource",
    "Matches",
    "MemoryKindSpec",
    "Model",
    "NonEmpty",
    "OneOf",
    "Origin",
    "Plugin",
    "PluginAPI",
    "Problem",
    "Reads",
    "Recognition",
    "RunContext",
    "TaskTypeSpec",
    "ToolContext",
    "ToolSpec",
    "ToolsetSpec",
    "asked_as",
    "scrub",
    "tool",
    "validate",
]
