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
