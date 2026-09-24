# 20: Fold the invariant-owning modules into `kernel/`

**What to build:** The modules that enforce Friday's safety invariants (§3.3) live under `friday/kernel/`, so "the kernel owns the invariants" is true of the folder layout, not only the prose — and the graph engine has one home instead of two. Pure structural move — no behaviour change.

**Blocked by:** 19.

**Priority:** highest — structural-cleanup track, before 16 and 17. Second of three slices (19 → 20 → 21). Move one cohesive group at a time, suite green after each, so no batch leaves a half-migrated tree.

**Status:** ready-for-agent

- [ ] `friday/dag/` and `friday/workflow/` merge into a single `friday/kernel/dag/` — the DBOS adapter (the one module importing `dbos`), the registry, the router, node 0 (`prepare`), `task_types`, and the state re-exports — so there is one graph home, not a `dag`/`workflow` split.
- [ ] The compatibility shims `dag/engine.py` and `dag/state.py` are **deleted**; their callers import from `friday.sdk.workflow` / `friday.sdk.workflow_state` directly.
- [ ] The harness (`friday/agent/` → `friday/kernel/harness/`, including the prompt assembly, structured output, MCP wiring, Keycloak auth, model-call logging and skill library), the outbox state machine (`friday/outbox/` → `friday/kernel/outbox/`), and the memory write path (`friday/memory/` + `memory_guard` → `friday/kernel/memory/`) live under `kernel/`.
- [ ] Stale docstrings fixed where the move exposes them (e.g. `dag/__init__.py`'s "a runner that checkpoints" — the runner is DBOS's now).
- [ ] Clean code: no dead re-export shims, no outdated path references left behind; every touched module reconciled to the new structure.
- [ ] `uv run pytest -q` passes; the dependency-rule and kernel-names-no-plugin guards stay green and non-vacuous; no behaviour change.
