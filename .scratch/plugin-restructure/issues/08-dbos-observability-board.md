# 08: DBOS observability in the existing board

**What to build:** The operator can watch running, queued, succeeded and failed workflows from the existing board — without a separate dashboard.

**Blocked by:** 06.

**Source:** `spec.md` — § Observability (reuse before rewrite).

**Status:** ready-for-agent

- [ ] A FastAPI endpoint returns workflow status from `DBOSClient.list_workflows(...)` + per-workflow progress events
- [ ] A workflow panel in the React board renders them, fed over SSE like the current Monitor
- [ ] No DBOS Conductor (the client API + the board cover observability)
- [ ] The axe and bundle-size gates still pass
- [ ] Clean code: remove the dead code, outdated comments and now-unused imports/functions this change leaves behind, and reconcile the modules it touched against the new `sdk`/`kernel`/`plugins` structure — nothing left in the old shape
- [ ] `uv run pytest -q` passes
