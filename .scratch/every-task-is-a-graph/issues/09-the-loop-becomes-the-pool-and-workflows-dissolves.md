# 09: The loop becomes the pool, and workflows/ dissolves

**What to build:** The loop is named for what it does — pull pending tasks,
host their graphs, act on what comes back — and lives in a package of its own.
The package that used to hold a second way of deciding what to do with a task
is deleted. The one new rule in this spec gets the loudest test in the suite.

**Blocked by:** 05, 06, 07, 08

**Decisions:** D5, and the invariant

**Status:** ready-for-agent

## Why

This is the contract step. Tickets 01–08 each moved something out of
`friday/workflows/` while leaving the package importable, so every one of them
could land green on its own. Folding the deletion into whichever of them
happened to land last would make that ticket's green depend on landing order,
which is not a property anyone can check while writing it.

What the loop actually owns is the task's lifecycle: stand down when the
operator answered, announce what nobody can act on, host the graph, turn its
outcome into rows. That is a pool engine, not a workflow, and it does not
belong inside the graph package either — hosting a graph is one of the four
things it does.

The invariant is new to this codebase and has no history of being enforced:
**only Responder-family agents produce text that reaches a reporter.** It is
the line most likely to be eroded quietly by a future ticket, because eroding
it breaks nothing — it just puts a different voice in the operator's mouth.

## Acceptance criteria

- [ ] The loop lives in a `tasks` package as the pool, and does four things:
      stand down, announce needs-human, host the graph, act on the outcome
- [ ] `friday/workflows/` is gone and nothing imports it; whatever was left in
      it lives beside its one caller
- [ ] Only Responder-family agents can produce text that reaches a reporter,
      pinned by a test that fails loudly and says why
- [ ] The composition root builds the pool by its new name and is otherwise
      unchanged — it still constructs adapters and starts tasks, and reads no
      agent's knobs
- [ ] Every assembled prompt is byte-identical before and after, captured
- [ ] CLAUDE.md and CONTEXT.md describe the result in this same commit — the
      layout table, the architecture constraints that named `workflows/`, and
      the vocabulary entries for the pool and for hand-over
