# 11: Task types register themselves

**What to build:** A task type is added by registering a `TaskTypeSpec` through `register()`, not by editing the router — proven by removing every `api_issue` literal from the router while triage accuracy is untouched.

**Blocked by:** 10.

**Source:** `spec.md` — Migration order, step 4 (task types register themselves).

**Status:** ready-for-agent

- [ ] `PARAMS`, `EXTRACTS`, `_graphs` replaced by a `TaskTypeSpec` registry filled by `register()`
- [ ] Graphs are expressed in the `sdk` workflow types
- [ ] `router.py` has no `api_issue` import or literal
- [ ] `run_agent.py` wires task types from the registry
- [ ] The triage prompt is untouched
- [ ] Clean code: remove the dead code, outdated comments and now-unused imports/functions this change leaves behind, and reconcile the modules it touched against the new `sdk`/`kernel`/`plugins` structure — nothing left in the old shape
- [ ] `uv run pytest -q` passes
