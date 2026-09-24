# 21: Fold the remaining app modules into `kernel/` — the tree matches DESIGN-v2

**What to build:** `friday/` matches DESIGN-v2's target tree: at the top level only `sdk/`, `kernel/`, `store/` and `plugins/` remain; every other application module lives under `kernel/`. The kernel-consolidation is complete — nothing left in the old shape. Pure structural move — no behaviour change.

**Blocked by:** 20.

**Priority:** highest — structural-cleanup track, before 16 and 17. Third of three slices (19 → 20 → 21).

**Status:** done

Delivered together with ticket 20 in one coordinated pass (see ticket 20 for
why the fold had to be atomic rather than group-by-group).

- [x] `triage/`, `extraction/`, `responder/`, `inbox/`, `ops/`, `text/`, `tasks/` (→ `friday/kernel/pool/`) and `tools/` move under `friday/kernel/`. (Also `providers/` → `friday/kernel/providers/`: box 1 omitted it, but box 3 forbids leaving it at the top level.)
- [x] The composition root's own pieces land in their kernel homes: `config.py` → `friday/kernel/config.py` (a module, not the literal `config/` package — functionally equivalent, one file), `plugin_host.py` → `friday/kernel/plugin_host.py` beside `kernel/registry.py`.
- [x] `friday/` top level holds only `sdk/`, `kernel/`, `store/` (plus the package `__init__.py` and the repo-root entry scripts); `plugins/` is the repo-root sibling per DESIGN-v2. No application module remains outside `kernel/` (verified `find friday -maxdepth 1`).
- [x] The composition root (`run_agent.py`) and the standalone scripts (`serve_board.py`, `replay_case.py`, `import_context_files.py`, and `authorize.py`) import from the new paths and still run (`import` smoke-tested OK).
- [x] Clean code: no dead code / outdated comments / stale path references left behind (historical closed-board ticket files under `.scratch/*/issues/` keep their original paths as a record, on purpose); every touched module reconciled.
- [x] `uv run pytest -q` passes (1568 passed, 1 skipped); every dependency-rule guard green and non-vacuous (`test_kernel_imports_only_sdk` relaxed to the single documented `friday.store` interim exception, tightening at ticket 16; `access_request` excepted from the literal guard as the in-core task type — both verified non-vacuous).
- [x] `docs/DESIGN.md` § Layout and `CONTEXT.md` reflect the final tree (kernel holds every app module; `store` named as the interim exception); no behaviour change.
