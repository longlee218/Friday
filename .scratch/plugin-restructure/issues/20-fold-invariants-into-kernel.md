# 20: Fold the invariant-owning modules into `kernel/`

**What to build:** The modules that enforce Friday's safety invariants (§3.3) live under `friday/kernel/`, so "the kernel owns the invariants" is true of the folder layout, not only the prose — and the graph engine has one home instead of two. Pure structural move — no behaviour change.

**Blocked by:** 19.

**Priority:** highest — structural-cleanup track, before 16 and 17. Second of three slices (19 → 20 → 21). Move one cohesive group at a time, suite green after each, so no batch leaves a half-migrated tree.

**Status:** done

Delivered together with ticket 21 in one coordinated pass (the operator's call:
folding one group at a time could not keep `test_kernel_imports_only_sdk` green,
because a group moved into `kernel/` still imported not-yet-folded app modules —
so all app modules moved at once, leaving only `friday.store` as the interim
exception). Committed as one change.

- [x] `friday/dag/` and `friday/workflow/` merge into a single `friday/kernel/dag/` — the DBOS `adapter.py` (the one module importing `dbos`), the registry, the router, node 0 (`prepare`), `task_types` — one graph home, no `dag`/`workflow` split.
- [x] The compatibility shims `dag/engine.py` and `dag/state.py` are **deleted**; their callers import from `friday.sdk.workflow` / `friday.sdk.workflow_state` directly (verified: zero `dag.engine`/`dag.state` importers remain).
- [x] The harness (`friday/agent/` → `friday/kernel/harness/`), the outbox state machine (`friday/outbox/` → `friday/kernel/outbox/`), and the memory write path (`friday/memory/` → `friday/kernel/memory/`) live under `kernel/`. (`memory_guard` is under `kernel/` too, at `friday/kernel/domain/memory_guard.py` where slice 19 placed it, rather than `kernel/memory/` — the invariant "under kernel/" holds.)
- [x] Stale docstrings fixed where the move exposes them (`dag/__init__.py`'s "a runner that checkpoints" rewritten; the deleted-shim sentence in `docs/DESIGN.md` struck).
- [x] Clean code: no dead re-export shims, no outdated path references left behind (dot-form + slash-form grep empty across `friday/ plugins/ tests/ evals/`); every touched module reconciled.
- [x] `uv run pytest -q` passes (1568 passed, 1 skipped); the dependency-rule and kernel-names-no-plugin guards stay green and non-vacuous (Rule 13: each verified red when violated); no behaviour change (every changed line in `friday/**/*.py` is an import or docstring/path edit).
