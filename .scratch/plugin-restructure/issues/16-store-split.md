# 16: Store split behind the Database facade

**What to build:** Kernel invariants leave `db.py` — the store becomes a set of repositories behind the `Database` facade, and the outbox and memory-write invariants live in kernel code, not inside the store.

**Blocked by:** 07, 14, 21 (the kernel-consolidation track 19→20→21 lands first — the store facade and the memory write path settle into `kernel/` before this splits `db.py`).

**Source:** `spec.md` — Migration order, step 9 (store split, workflow state excluded).

**Status:** ready-for-agent

- [ ] `db.py` is split into repository modules behind the same `Database` facade
- [ ] Outbox transitions and memory writers move into kernel code
- [ ] Workflow state is excluded from the split (DBOS owns it)
- [ ] Test: a `Store` fake that approves on its own is ignored by the outbox (the invariant is not enforced inside the store)
- [ ] Clean code: remove the dead code, outdated comments and now-unused imports/functions this change leaves behind, and reconcile the modules it touched against the new `sdk`/`kernel`/`plugins` structure — nothing left in the old shape
- [ ] `uv run pytest -q` passes
