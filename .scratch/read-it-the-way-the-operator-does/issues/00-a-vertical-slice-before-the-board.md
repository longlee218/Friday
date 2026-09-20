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

**Status:** ready-for-agent — cases 2–5, and case 1's cause, are
`ready-for-human`

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

## The five cases

One heading each. What Friday already holds is filled in; the cause and the
decisive log line are the operator's to write, and a case is not usable
until they are there — a slice scored against a cause the model proposed
measures nothing.

### Case 1 — POD `orders/init` on dev, 2026-09-20 (task 6)

Not a remembered case. It arrived while this board was open, and it ran the
one-node graph live, which is why it is worth keeping: the numbers below are
measured, not estimated.

| | |
|---|---|
| Task | `tasks` id 6, conversation `discord:1544369941296189591` |
| Board | `/flow/discord/1551090724089503787` |
| Endpoint | `POST /v1/pod/orders/init` |
| Domain | `api-reelme-v2.dev.aperogroup.ai` → dev, by D1's rule |
| Pod | `backend-reelme-v2`, namespace `dev`, reached by `ssh dev` (16's measurement 3) |
| Triage | `api_issue`, confidence 0.92 — right |
| Extracted | `environment: dev`, `curl` verbatim, `correlation_id: null` |
| What ran | one node, `prepare`, attempt 1, `ok`, 13.3 s |
| Where it stopped | `HandOver` → `needs_human`; outbox row 22 (`help_wanted`) sent 04:41:19 |
| Operator owes | the cause, and the log line that names it |

**Why it stopped is not a defect.** `_traceable` is `OneOf(correlation_id,
curl)` and the curl is there; `environment` is in the set; nothing is
missing, so `plan_by_required_parameters` has nothing to `Ask` and returns
`HandOver` (`friday/dag/prepare.py:305`). The hand-over sentence is true:
`api_issue` is a one-node graph, and this ticket is what puts nodes past it.

**Worth noting for the board card.** The card showed the extractor's
`summary` — "gửi curl … nhưng chưa mô tả cụ thể lỗi đang gặp" — beside a
`needs_human` state, which reads as though the missing error description is
why Friday stopped. It is not; it is a one-line description of the message.
If that reading recurs, it belongs in the monitor's board, not here.

**The curl it stored is not the curl that was sent.** Found while writing
this case up: `params.curl` dropped one character out of the Bearer token's
base64, so the stored request fails its signature check and returns 401 —
an error the reporter never saw. Ticket 18. This case cannot be scored
until that is fixed or the curl is corrected by hand, because the slice's
whole premise is reaching the reporter's own request.

**The harder branch.** There is no correlationId, because the reporter
pasted the *request*, not the response — exactly what D2 predicts. So
`FindRequestLog` cannot filter on an id, and the routine's step 3 falls
through to "approximately, by path and timestamp", then to replay. See the
open question.

Answers 1–4: owed, once the slice runs.

### Cases 2–5

Not chosen. The operator's to pick, from `api_issue` tasks whose cause they
still remember. Two of them should have a correlationId, so both branches of
`FindRequestLog` are exercised.

## Open question, raised by case 1 (2026-09-20)

**May Friday replay a request?** Step 3 of the operator's own routine ends
in replaying the curl when the log cannot be found, and case 1 lands on that
branch with nothing else left to try. No ticket on this board owns replay,
and it is the first thing asked of Friday that is not a read: a replayed
`POST /v1/pod/orders/init` creates a real order on dev. It also sits against
"Friday never writes code" — read tools only, which is the line this board
has held throughout.

Until that is answered, this slice's `FindRequestLog` has two branches, not
three — by id, or by path and timestamp — and a case with neither ends in
`not_checked` naming what it did not try. Case 1 is the test of whether that
is enough to be useful.
