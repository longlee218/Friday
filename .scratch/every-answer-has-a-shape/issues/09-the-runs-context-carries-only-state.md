# 09: The run's context carries only state

**What to build:** the guard that closes the loop. The SDK's per-run context
means one thing from here on — the run's state — so the slot cannot again mean
"who is this run about" for one agent and "where the answer will appear" for
another (D8, D30 of the stories). No tool writes into it; every agent that takes
a context takes the state.

**Blocked by:** 06, 07, 08.

**Status:** ready-for-agent

- [ ] No tool writes into the run context, and a test reads the syntax rather than trusting the prose.
- [ ] Every agent built with a context type is built with the state's type.
- [ ] The capture classes are gone, along with the tests that knew how an answer travelled.
- [ ] CLAUDE.md describes the one meaning the slot now has.

## Comments

**Not started — blocked.** Ticket 07 needs ticket 03’s baseline (D16: rebuild
the ruler, read today’s code with it, then change the code), and this one needs
07.

Half of what it guards is already true: the extractor stopped using a capture
in ticket 08, and the responder carries `FridayState`. What is left is triage’s
`ClassifyCapture` — the last thing riding the run context that is not state —
and the `ast` guard that says no tool writes into `ctx.context`.
