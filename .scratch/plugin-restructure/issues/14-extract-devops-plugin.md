# 14: Extract friday/sdk + plugins/devops

**What to build:** The `api_issue` work becomes `plugins/devops` against the `sdk` ports, with the kernel knowing nothing of it — the split proven on the real task type.

**Blocked by:** 11, 12, 13.

**Source:** `spec.md` — Migration order, step 7 (`sdk/` + `plugins/devops/`).

**Status:** ready-for-agent

- [ ] The ports `devops` needs are extracted into `friday/sdk` (`LogSource`, `CodeSource`, `TaskTypeSpec`, `MemoryKindSpec`, `Deps`, workflow port)
- [ ] `api_issue`, `sources/`, devops kinds, skills and `ApiIssueConfig` (→ plugin schema) move to `plugins/devops`
- [ ] A data migration renames devops kinds to `devops.*` across every column holding a type or kind name (`tasks.type`, triage examples and verdicts, `node_runs`, `model_calls.agent`), each grep-checked before the migration is written; `runbook` rows become `skill` rows
- [ ] `sources/code.py` container roots / vendored paths become data (config or a memory row), not module constants
- [ ] Kernel `ast` test passes (no plugin import, no task-type literal)
- [ ] `uv run pytest -q` passes
- [ ] Triage eval re-run and reported (accuracy, confusion matrix, threshold table)
