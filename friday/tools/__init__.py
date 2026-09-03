"""Every tool an agent may call.

One module per subject: asking (`clarify`, `ask_for_fields`), classifying
(`classify`), reaching (`fetch_skill`).

CLAUDE.md said for months there was deliberately no `tools/` package, and the
argument was that a tool belongs beside the state it touches because that is
the only place its guard can be enforced. That argument did not survive the
question "what can the agents actually do?", which has to be answerable and
was not: the tools were spread across four modules, and two of them were
invisible to a grep for `@tool` because they are wrapped by calling `tool(fn)`
after `__doc__` is assigned.

`tests/test_tools.py` asserts the list and forbids declaring a tool anywhere
else, reading the syntax rather than grepping so both spellings are caught.

**`ask_clarification` has no caller yet**, and is kept for one. `answer`,
`hand_over` and `apply_fix` went with the five-node `api_issue` graph that was
their only user; `remember` went because nothing gave it to an agent and it
was not a tool at all — a factory returning a plain async function.

Empty of code on purpose: importing any submodule runs this first.
"""
