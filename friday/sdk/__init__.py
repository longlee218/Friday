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
from friday.sdk.plugin import Plugin, PluginAPI, TaskTypeSpec

__all__ = [
    "MemoryKindSpec",
    "Origin",
    "Plugin",
    "PluginAPI",
    "TaskTypeSpec",
]
