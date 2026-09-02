# 01: The vocabulary moves to the domain

**What to build:** The words the whole system uses for "what a decision about
a task came to" — ask, reply, hand it to a person — live in the domain
package, and the module holding task states is named for what it holds.
Behaviour is unchanged, prompts included, byte for byte.

**Blocked by:** None (can start immediately)

**Decisions:** D3, D6

**Status:** done

## Why

`friday/dag/api_issue.py` imports `Ask`, `Park` and `Reply` from
`friday.workflows`, and the loop in `friday.workflows` imports the whole graph
engine. Engine and loop import each other, and the only thing crossing between
them in one direction is vocabulary — which belongs to neither of them. Every
later ticket in this spec moves one of those two modules; doing it while they
hold each other's names means each move drags the other along.

The state module is the second half of the same tidy-up. It is named for tasks
and holds `TaskState` and `OutboundState` — it was already wrong — and D5 puts
the loop in a `tasks` package, which would collide with it outright.

## Acceptance criteria

- [x] `Ask`, `Reply`, `Park` and the `Action` union are defined in the domain
      package, and both the graph engine and the loop import them from there
- [x] Neither the graph package nor the loop imports the other for vocabulary
- [x] `TaskState`, `OutboundState`, the legal-move table, the open-states set
      and `may_move` live in a domain module named for states; no import of
      the old module name survives anywhere, tests and migrations included
- [x] Every assembled prompt is byte-identical before and after, captured with
      the golden script used for tickets 42–45 rather than eyeballed
- [x] The suite passes with nothing changed in a test but its imports

## What it came to

`friday/domain/actions.py` is new — `Ask`, `Reply`, `Park`, `Action`, moved
verbatim. `friday/domain/tasks.py` is `friday/domain/states.py` now, `git mv`
so the history follows. Every caller updated to import from the new places:
`friday/dag/api_issue.py`, `friday/workflows/__init__.py`,
`friday/workflows/runner.py`, five more modules under `friday/`, and eleven
test files. `friday.workflows` still re-exports `Ask`/`Reply`/`Park`/`Action`
in `__all__` for its own internal use (`prepare`, `plan_by_required_parameters`
still return an `Action`) — it stops mattering once ticket 09 deletes the
package.

No `friday/*/prompt.py`, `PERSONA.md` or `config.yaml` was touched, so byte
identity holds by construction; the existing prompt tests confirm it.

A new hygiene test,
`test_the_graph_engine_and_the_loop_do_not_import_each_other_for_vocabulary`,
pins the cycle staying gone. Removed and watched go red before being kept.

609 tests before, 610 after (the new guard). CLAUDE.md and CONTEXT.md's
`domain/` references were corrected in the same commit.
