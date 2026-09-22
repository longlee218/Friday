# 04: Single-instance lock + busy_timeout

**What to build:** Two agent processes can never run at once — a second `run_agent` refuses to start while one holds the lock — and a concurrent board read no longer fails a write.

**Blocked by:** 01.

**Status:** ready-for-agent

- [ ] `run_agent.py` takes an exclusive lock (a lock file beside the database) at startup
- [ ] A second `run_agent` refuses to start with a clear message
- [ ] SQLite `busy_timeout` set explicitly
- [ ] Test: a second `run_agent` refuses to start (guard deleted once and watched go red)
- [ ] `uv run pytest -q` passes
