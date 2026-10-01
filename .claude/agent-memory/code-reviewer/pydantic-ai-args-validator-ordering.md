---
name: pydantic-ai-args-validator-ordering
description: pydantic-ai's ToolManager always runs a Tool's args_validator before its function in the real agent loop; calling tool.function(...) directly bypasses it
metadata:
  type: project
---

Verified against the installed `pydantic_ai` package (`tool_manager.py`,
`_validate_tool_args`, and `tools.py`'s `Tool.__init__`): `args_validator=`
is a first-class `Tool` constructor argument, and the normal agent loop
(`ToolManager`) always calls it before invoking the tool's own function.
`Tool(spec.fn, **spec.options)` in `friday/kernel/harness/harness.py`
(`_bind_tool_spec`) passes `description=`/`prepare=`/`args_validator=`
straight through from a plugin's `ToolSpec`.

**Why:** build-the-spine ticket 23 (`friday/kernel/toolsets/repos.py`) puts
every semantic refusal (unknown repo, path outside the clone, a secret
file/dir) in `args_validator=` rather than duplicating it in the tool body,
on the explicit assumption that the validator always runs first inside a
real pydantic-ai agent run. Confirmed true; a direct `tool.function(...)`
call (bypassing the validator) is only reachable from test code or a bug
elsewhere — grep the tree for non-test `.function(` calls to confirm nothing
does this in production code before trusting the assumption again.

**How to apply:** when reviewing a tool that puts checks only in
`args_validator=` (the pattern ticket 26 generalizes to every tool), don't
flag it as an exploitable hole from within the normal agent loop — verify
instead that (a) no production code calls `tool.function(...)` directly, and
(b) tests that call the function directly also call `args_validator` first
(the `call()` helpers in `tests/test_core_repos.py`,
`tests/test_investigate_tools.py`, `tests/test_backend_toolsets.py` do this).
See [[friday-agents-project-layout]] if that memory exists for where these
tool modules live.
