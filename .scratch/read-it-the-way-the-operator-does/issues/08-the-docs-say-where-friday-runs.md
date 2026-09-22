# 08: The docs say where Friday runs, and that api_issue has a graph again

**What to change:** `CLAUDE.md`, `CONTEXT.md`, `docs/DESIGN.md`.

**Blocked by:** 06's outbound half (2026-09-22), and only for the part
that describes it.

**Decisions:** D14, and the record of all of them.

**Status:** part done, and two of its bullets are already true
(2026-09-22). `CLAUDE.md` no longer mentions running on a server — it is
how-to-work only now, since ticket 10 — and `register_dags` does not say
`api_issue` is one node. `CONTEXT.md` and `docs/DESIGN.md` have been kept
up as the slice landed, including the Source layer, the narrowing decision
and ticket 00's answers.

**What is left:** D1–D14 are not summarised in one place in
`docs/DESIGN.md`; the `CONTEXT.md` vocabulary entries for Route, Finding
and Report have not been checked against what those words now mean; and the
outbound half of 06 cannot be documented before it exists.

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
