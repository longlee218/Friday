# 01: The vocabulary moves to the domain

**What to build:** The words the whole system uses for "what a decision about
a task came to" — ask, reply, hand it to a person — live in the domain
package, and the module holding task states is named for what it holds.
Behaviour is unchanged, prompts included, byte for byte.

**Blocked by:** None (can start immediately)

**Decisions:** D3, D6

**Status:** ready-for-agent

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

- [ ] `Ask`, `Reply`, `Park` and the `Action` union are defined in the domain
      package, and both the graph engine and the loop import them from there
- [ ] Neither the graph package nor the loop imports the other for vocabulary
- [ ] `TaskState`, `OutboundState`, the legal-move table, the open-states set
      and `may_move` live in a domain module named for states; no import of
      the old module name survives anywhere, tests and migrations included
- [ ] Every assembled prompt is byte-identical before and after, captured with
      the golden script used for tickets 42–45 rather than eyeballed
- [ ] The suite passes with nothing changed in a test but its imports
