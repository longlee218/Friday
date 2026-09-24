# 16: Store split behind the Database facade

**What to build:** Kernel invariants leave `db.py` — the store becomes a set of repositories behind the `Database` facade, and the outbox and memory-write invariants live in kernel code, not inside the store.

**Blocked by:** 07, 14, 21 (the kernel-consolidation track 19→20→21 lands first — the store facade and the memory write path settle into `kernel/` before this splits `db.py`).

**Source:** `spec.md` — Migration order, step 9 (store split, workflow state excluded).

**Status:** done

Two operator scope decisions this session: (a) the **Store Protocol in the sdk
is deferred** (the kernel calls ~70 store methods, so a faithful Protocol would
duplicate all of them — DESIGN-v2's own reason for deferring it); the kernel
keeps importing the concrete `friday.store`, the one documented interim exception
in `test_dependency_rule.py`. (b) the memory writers are a **full move**: the
store is dumb about the trust-boundary invariants, which live only in the kernel.

- [x] `db.py` is split into repository modules behind the same `Database` facade. The 3771-line god-class became eight mixins under `friday/store/repositories/` (`messages`, `memory`, `outbox`, `tasks`, `calls`, `rooms`, `monitor`, `verdicts`), with shared imports/converters/constants in `friday/store/_common.py`; `Database` composes them (method bodies moved byte-for-byte — both reviewers confirmed all 119 methods reachable, none lost or duplicated).
- [x] Outbox transitions and memory writers move into kernel code. The outbox approval gate is authoritative in `friday/kernel/outbox/` `deliver_once` (the store's `sendable_outbound` filter is now only an optimisation); the memory trust-boundary invariants (instruction-shaped guard + origin/`writers_for`) moved to `friday/kernel/memory/write.py` and were removed from the store, with every runtime caller (tools/memory, ops/api, channel_context, import_context_files) rerouted through `write.*`.
- [x] Workflow state is excluded from the split (DBOS owns it) — nothing under `dag/`/`workflow` touched.
- [x] Test: a `Store` fake that approves on its own is ignored by the outbox — `tests/test_outbox.py::test_a_store_that_approves_a_reply_on_its_own_is_ignored` (Rule 13 verified: red when the gate is removed). Plus `tests/test_memory_guard.py::test_the_kernel_write_path_guards_a_dumb_store` for the memory half.
- [x] Clean code: the store's guard imports (`check_not_instruction_shaped`, `InstructionShaped`, `writers_for`) removed (grep-clean), docstrings and `docs/DESIGN.md`/`CONTEXT.md` reconciled to the facade-over-repositories shape and to "the invariants live in the kernel"; the deferred-Protocol wording corrected everywhere.
- [x] `uv run pytest -q` passes (1570 passed, 1 skipped).
