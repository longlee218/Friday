# 13: Typed per-run Deps from the scope key

**What to build:** A run's `Deps` are built per run from the run's scope key, replacing the ad-hoc `DAG_DEPS_EXTRA`, so a workflow's persisted input stays serializable while its handles are live.

**Blocked by:** 11.

**Source:** `spec.md` — Migration order, step 6 (typed per-run Deps from the scope key).

**Status:** done

- [x] A task type's `deps` factory builds `Deps` at run start from the serializable scope key
- [x] `DAG_DEPS_EXTRA` is removed
- [x] A boot check confirms every `deps` field can be satisfied
- [x] Guard deleted once and watched go red
- [x] Clean code: remove the dead code, outdated comments and now-unused imports/functions this change leaves behind, and reconcile the modules it touched against the new `sdk`/`kernel`/`plugins` structure — nothing left in the old shape
- [x] `uv run pytest -q` passes
