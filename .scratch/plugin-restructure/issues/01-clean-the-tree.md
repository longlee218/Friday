# 01: Clean the tree

**What to build:** A clean starting point for the migration — the uncommitted `api_issue` work is landed or stashed and the params-migration fix is committed, so every later ticket branches from a green, known state.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [x] Uncommitted `api_issue` work is landed or stashed
- [x] The params-migration fix is committed
- [x] `git status` is clean
- [x] `uv run pytest -q` passes
