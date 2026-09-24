# 21: Fold the remaining app modules into `kernel/` — the tree matches DESIGN-v2

**What to build:** `friday/` matches DESIGN-v2's target tree: at the top level only `sdk/`, `kernel/`, `store/` and `plugins/` remain; every other application module lives under `kernel/`. The kernel-consolidation is complete — nothing left in the old shape. Pure structural move — no behaviour change.

**Blocked by:** 20.

**Priority:** highest — structural-cleanup track, before 16 and 17. Third of three slices (19 → 20 → 21).

**Status:** ready-for-agent

- [ ] `triage/`, `extraction/`, `responder/`, `inbox/`, `ops/`, `text/`, `tasks/` (→ `friday/kernel/pool/`) and `tools/` move under `friday/kernel/`.
- [ ] The composition root's own pieces land in their kernel homes: `config.py` → `friday/kernel/config/`, `plugin_host.py` → the kernel registry area.
- [ ] `friday/` top level holds only `sdk/`, `kernel/`, `store/`, `plugins/` (plus the package `__init__.py` and the repo-root entry scripts). No application module remains outside `kernel/`.
- [ ] The composition root (`run_agent.py`) and the standalone scripts (`serve_board.py`, `replay_case.py`, `import_context_files.py`) import from the new paths and still run.
- [ ] Clean code: no dead code / outdated comments / stale path references left behind; every touched module reconciled to the final structure.
- [ ] `uv run pytest -q` passes; every dependency-rule guard green and non-vacuous.
- [ ] `docs/DESIGN.md` § What exists and `CONTEXT.md` reflect the final tree; no behaviour change.
