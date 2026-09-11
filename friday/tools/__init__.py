"""Every tool an agent may call.

One module per subject. **The list is not written here**, because a list in
prose is a list that drifts: this sentence named four of the twelve tools that
exist, having quietly stopped tracking three skill tools and then the whole
memory subject. `tests/test_tools.py` asserts the names, builds the factories
as well as scanning the modules, and fails if a new one is missing — so the
answer to "what can the agents do?" lives in the one place that cannot be
wrong about it. Read it there; the subjects are the module names beside this
file.

CLAUDE.md said for months there was deliberately no `tools/` package, and the
argument was that a tool belongs beside the state it touches because that is
the only place its guard can be enforced. That argument did not survive the
question "what can the agents actually do?", which has to be answerable and
was not: the tools were spread across four modules, and two of them were
invisible to a grep for `@tool` because they are wrapped by calling `tool(fn)`
after `__doc__` is assigned.

`tests/test_tools.py` asserts the list and forbids declaring a tool anywhere
else, reading the syntax rather than grepping so both spellings are caught.

**Nothing here is kept for a caller that has not arrived.** `answer`,
`hand_over` and `apply_fix` went with the five-node `api_issue` graph that was
their only user; `remember` went because nothing gave it to an agent and it
was not a tool at all — a factory returning a plain async function; and
`ask_clarification` went for the reason it should have gone sooner. It took a
question in words, it was written for an agent that never got it, and this
docstring said "kept for one" for months — so every reader who met it had to
work out for themselves that nothing called it. Board
`every-answer-has-a-shape`, ticket 01, D14: a door that is not in the room is
deleted rather than described.

Empty of code on purpose: importing any submodule runs this first.
"""
