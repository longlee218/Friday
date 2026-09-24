"""Friday's SDK surface: the contracts a plugin registers against.

Protocols and dataclasses only — no I/O, no third-party imports. What the kernel
builds on and what a plugin codes against, so a plugin never reaches into the
core. The workflow port (`workflow.py`) landed in ticket 06; the plugin and
memory contracts (`plugin.py`, `memory.py`) in ticket 10; the test-double seam
(`testing/`) in ticket 05.

The dependency rule (`tests/test_dependency_rule.py`) holds this in place: `sdk`
imports nothing of ours but the `friday.domain` value layer beneath it, `kernel`
imports `sdk`, and a plugin imports `sdk` only.
"""

from friday.sdk.memory import MemoryKindSpec, Origin
from friday.sdk.model import Model
from friday.sdk.outbox import Kind
from friday.sdk.plugin import Plugin, PluginAPI, TaskTypeSpec
from friday.sdk.redact import scrub
from friday.sdk.sources import CodeSource, Lines, LogSource, Placement, Reads
from friday.sdk.tools import ToolContext, ToolSpec, tool
from friday.sdk.validation import InSet, Matches, NonEmpty, OneOf, Problem, asked_as, validate

__all__ = [
    "CodeSource",
    "InSet",
    "Kind",
    "Lines",
    "LogSource",
    "Matches",
    "MemoryKindSpec",
    "Model",
    "NonEmpty",
    "OneOf",
    "Origin",
    "Placement",
    "Plugin",
    "PluginAPI",
    "Problem",
    "Reads",
    "TaskTypeSpec",
    "ToolContext",
    "ToolSpec",
    "asked_as",
    "scrub",
    "tool",
    "validate",
]
