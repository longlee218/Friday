# 08: The docs say where Friday runs, and that api_issue has a graph again

**What to change:** `CLAUDE.md`, `CONTEXT.md`, `docs/DESIGN.md`.

**Blocked by:** 06.

**Decisions:** D14, and the record of all of them.

**Status:** ready-for-agent

## What

- `CLAUDE.md`: "Running it on a server" becomes "running it on the
  operator's machine", and says why every graph tool is a read (D6, D14).
  The Status paragraph names this board. `register_dags`'s docstring and
  the "Every task type is a graph" constraint stop saying `api_issue` is
  one node.
- `CONTEXT.md`: terms for Route, Finding (if absent), Report.
- `docs/DESIGN.md`: D1–D14 summarised, with the measured facts pointed at
  rather than restated.
- Delete the leftover `friday/dag/api_issue/__pycache__/`.
