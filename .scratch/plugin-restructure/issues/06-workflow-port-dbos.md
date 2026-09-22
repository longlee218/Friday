# 06: Workflow port + migrate the engine onto DBOS

**What to build:** Durable workflows run on DBOS behind a thin `sdk/workflow.py` port; the hand-written DAG engine is retired and its behaviour is preserved. The engine underneath is a library, but the contract plugins code against stays stable.

**Blocked by:** 01. (Recommended after 05.)

**Source:** `spec.md` — Migration order, step 3 (DBOS workflow port + adapter); § Implementation Decisions → "Runtime libraries" (DESIGN-v2 §7).

**Status:** ready-for-agent

- [ ] `sdk/workflow.py` is a thin Friday port (`Node`/`Step`/`Edge`, envelope, `Ask`/`Reply`/`HandOver`)
- [ ] DBOS is the adapter beneath the kernel; nothing outside the adapter imports `dbos`
- [ ] DBOS runs on its own SQLite system-database file (`system_database_url = sqlite:///…`), separate from Friday's application database
- [ ] The derived `DAG.version` source-digest scheme is removed; recovery is DBOS's (application version + per-step memoization)
- [ ] A workflow's input is a serializable scope key; `Deps` are rebuilt inside the run
- [ ] The kernel chain still wraps each step (budget, recording, redaction, `needs`/`side_effect`)
- [ ] Behaviour preserved: the S1 message-path slices and the `api_issue` replay eval stay green
- [ ] Real-DBOS test on SQLite: kill mid-workflow → restart → resume from the last incomplete step
- [ ] `uv run pytest -q` passes
