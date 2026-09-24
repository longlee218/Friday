# 14: Extract friday/sdk + plugins/devops

**What to build:** The `api_issue` work becomes `plugins/devops` against the `sdk` ports, with the kernel knowing nothing of it — the split proven on the real task type.

**Blocked by:** 11, 12, 13.

**Source:** `spec.md` — Migration order, step 7 (`sdk/` + `plugins/devops/`).

**Status:** done

- [x] The ports `devops` needs are extracted into `friday/sdk` (`LogSource`, `CodeSource`, `TaskTypeSpec`, `MemoryKindSpec`, `Deps`, workflow port) — plus the seams full purity needs (`prompt`/`redact`/`validation` re-exported from `friday.domain`, `tools`, `model`, `outbox`)
- [ ] `api_issue`, `sources/`, devops kinds, skills and `ApiIssueConfig` (→ `DevopsConfig`) move to `plugins/devops` — **done except the skill *files***: `api_issue` (→ `graph/`), `sources/`, the `devops.*` pack kinds (→ `memory.py`) and `ApiIssueConfig` all moved and the plugin imports `friday.sdk` only. The two devops skill dirs (`trace-a-request`, `where-to-find-a-correlation-id`) stay in the shared `skills/` directory: moving them needs a multi-root `SkillLibrary` + a `PluginAPI.skills` method, which `friday/sdk/plugin.py` explicitly defers to its own ticket (DESIGN-v2 §4.2). Deferred, not dropped.
- [x] A data migration renames devops kinds to `devops.*` across every column holding a type or kind name (`tasks.type`, triage examples and verdicts, `node_runs`, `model_calls.agent`), each grep-checked before the migration is written; `runbook` rows become `skill` rows — migration `7f31db1381ef`, also covering `messages.decision_type`, `dag_state.dag_name`, `memory_candidates.kind` and the agent columns; round-trip tested on a throwaway DB
- [x] `sources/code.py` container roots / vendored paths become data (config or a memory row), not module constants — `DevopsConfig.container_roots`/`not_ours`, carried on `ApiIssueDeps`
- [x] Kernel `ast` test passes (no plugin import, no task-type literal) — and `test_a_plugin_imports_sdk_only` is now non-vacuous (21 plugin modules), verified to bite
- [x] Clean code: remove the dead code, outdated comments and now-unused imports/functions this change leaves behind, and reconcile the modules it touched against the new `sdk`/`kernel`/`plugins` structure — nothing left in the old shape
- [x] `uv run pytest -q` passes — 1568 passed, 1 skipped
- [x] Triage eval re-run and reported (accuracy, confusion matrix, threshold table) — 35 examples, 94.3%, 0/35 outside the closed set; the new `devops.api_issue` label used correctly (the 2 misses were provider timeouts, not misclassifications)
