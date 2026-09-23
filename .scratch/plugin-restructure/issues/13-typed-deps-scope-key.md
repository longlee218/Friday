# 13: Typed per-run Deps from the scope key

**What to build:** A run's `Deps` are built per run from the run's scope key, replacing the ad-hoc `DAG_DEPS_EXTRA`, so a workflow's persisted input stays serializable while its handles are live.

**Blocked by:** 11.

**Source:** `spec.md` — Migration order, step 6 (typed per-run Deps from the scope key).

**Status:** ready-for-agent

- [ ] A task type's `deps` factory builds `Deps` at run start from the serializable scope key
- [ ] `DAG_DEPS_EXTRA` is removed
- [ ] A boot check confirms every `deps` field can be satisfied
- [ ] Guard deleted once and watched go red
- [ ] Clean code: remove the dead code, outdated comments and now-unused imports/functions this change leaves behind, and reconcile the modules it touched against the new `sdk`/`kernel`/`plugins` structure — nothing left in the old shape
- [ ] `uv run pytest -q` passes
