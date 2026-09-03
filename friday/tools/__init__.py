"""Every tool an agent may call.

One module per subject: asking (`clarify`, `ask_for_fields`), concluding
(`reply`, `classify`), acting (`patch`), reaching (`fetch_skill`).

CLAUDE.md said for months there was deliberately no `tools/` package, and the
argument was that a tool belongs beside the state it touches because that is
the only place its guard can be enforced. That argument did not survive the
question "what can the agents actually do?", which has to be answerable and
was not: the tools were spread across four modules, and two of them were
invisible to a grep for `@tool` because they are wrapped by calling `tool(fn)`
after `__doc__` is assigned. Nor was the guard part true — what gates
`apply_fix` is `needs_approval=True` on the decorator, which travels with the
function.

`tests/test_tools.py` asserts the list and forbids declaring a tool anywhere
else, reading the syntax rather than grepping so both spellings are caught.

**`ask_clarification` has no production caller yet.** It is built and tested;
no agent is given it. That is a half-finished requirement rather than dead
code, and it is named here so the next reader does not have to work out which.

Empty of code on purpose: importing any submodule runs this first.
"""
