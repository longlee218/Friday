# 06: MemoryScope is deleted

**What to build:** the contract half of ticket 04. One notion of "which room is
this", not two — a second name for one thing is how the two drift (D10). Every
caller, every store method and every test moves to the state, and the old name is
removed rather than kept as an alias.

**Blocked by:** 04.

**Status:** done

- [x] The old name appears nowhere in production code or tests.
- [x] Every memory write and search reaches the store through the state.
- [x] The suite is green, and the memory-guard and responder tests still assert what they asserted before.

## Comments

Folded into ticket 04 — see its Comments for why. `friday/tools/memory.py`’s
`_scope` is `_state`, named for what it returns; the store’s parameter stays
`scope: FridayState`, where it really is the state *used as* a scope and the
name says which of its jobs that call is asking for.
