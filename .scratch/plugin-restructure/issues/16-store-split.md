# 16: Store split behind the Database facade

**What to build:** Kernel invariants leave `db.py` — the store becomes a set of repositories behind the `Database` facade, and the outbox and memory-write invariants live in kernel code, not inside the store.

**Blocked by:** 07, 14.

**Status:** ready-for-agent

- [ ] `db.py` is split into repository modules behind the same `Database` facade
- [ ] Outbox transitions and memory writers move into kernel code
- [ ] Workflow state is excluded from the split (DBOS owns it)
- [ ] Test: a `Store` fake that approves on its own is ignored by the outbox (the invariant is not enforced inside the store)
- [ ] `uv run pytest -q` passes
