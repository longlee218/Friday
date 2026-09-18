# 00: A vertical slice before the board

**What to build:** The thinnest line that reaches a real diagnosis through
the real pool, outbox and board — `Prepare → Resolve → FindRequestLog →
ReadFailingCode → Diagnose → Report` — and run it on five past cases whose
cause the operator still remembers.

**Blocked by:** 12 (the approval-per-task defect is the first integration
bug this slice would hit). 16 runs alongside; its measurements set this
slice's numbers.

**Decisions:** the operator's 2026-09-18 call, after the design was scored:
"nine tickets' worth of green suite did not catch three bugs" (`CLAUDE.md`),
and this design has changed four times in two days without running once.

**Status:** ready-for-agent

## What is in, and what is deliberately out

In: `Resolve` reading **two `route` rows typed by hand** for ReelMe (dev and
production) — not ticket 09's twelve kinds; `FindRequestLog` over Loki
through the MCP, and over kubectl, with the recall-first distillation rule;
`ReadFailingCode` from the stack frame, on the operator's clone at its
current HEAD (not the running tag); `Diagnose` with **no tools** and the
answer shape's grounding gate; `Report` writing the file and returning
`HandOver` only. The envelope `status` field, since `Report` needs it.

Out: `Notify`, `Explain`, the brief, the Collector, `Gather`'s other checks,
the dossier budget (the slice uses fixed line caps), memory writes, the UI.

## What it has to answer

For each of the five cases, written into this ticket:
1. Did the distilled dossier contain the line the operator names as
   decisive? (feeds 16's coverage test)
2. Did `Diagnose` cite a ref that exists, and was the cause right?
3. Dossier tokens, wall time per node, model calls.
4. Which integration seam broke — pool, outbox, checkpoint, board — and how.

## Verify

- Five runs through `run_agent.py` against a throwaway database, with the
  five reports and the four answers above recorded here.
- Whole suite green; nothing of this slice is kept if a later ticket
  replaces it — it is allowed to be thrown away.
