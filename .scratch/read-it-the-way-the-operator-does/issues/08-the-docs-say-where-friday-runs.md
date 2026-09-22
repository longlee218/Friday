# 08: The docs say where Friday runs, and that api_issue has a graph again

**What to change:** `CLAUDE.md`, `CONTEXT.md`, `docs/DESIGN.md`.

**Blocked by:** nothing in code (2026-09-22); `CONTEXT.md` has another
session's uncommitted work in it.

**Decisions:** D14, and the record of all of them.

**Status:** part done (2026-09-22). D1–D14 are indexed in `docs/DESIGN.md`
— one line each, naming where the decision lives in the code and what
amended it, rather than a second copy of the spec to keep in step. `CLAUDE.md`
and `register_dags` were already correct.

**What is left:** the `CONTEXT.md` vocabulary entries for Route, Finding and
Report. That file carries uncommitted work from another session and is not
this ticket's to stage.

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
