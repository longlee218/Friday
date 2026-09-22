# 04: Single-instance lock + busy_timeout

**What to build:** Two agent processes can never run at once — a second `run_agent` refuses to start while one holds the lock — and a concurrent board read no longer fails a write.

**Blocked by:** 01.

**Source:** `spec.md` — Migration order, step 1 (library-independent defects); § Local operation (§12.1).

**Status:** done

- [x] `run_agent.py` takes an exclusive lock (a lock file beside the database) at startup
- [x] A second `run_agent` refuses to start with a clear message
- [x] SQLite `busy_timeout` set explicitly
- [x] Test: a second `run_agent` refuses to start (guard deleted once and watched go red)
- [x] `uv run pytest -q` passes
