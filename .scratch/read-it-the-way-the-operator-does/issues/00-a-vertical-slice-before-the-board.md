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

**Status:** the code is built (2026-09-20, see the last section); the five
runs are not. `ready-for-human`: cases 2–5, case 1's cause, and the `route`
and `service` rows the slice reads

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

**The curl it stored is not the curl that was sent** — but the one that was
sent is still here. `params.curl` dropped one character out of the Bearer
token's base64, so the stored request fails its signature check and returns
401, an error the reporter never saw. Ticket 18 fixed the mechanism, and
while fixing it found that the reporter's own bytes were never lost:
`artifacts.af85b208fd70e`, from message `1551090724089503787`, holds the
curl whole — 1155 characters, its token the full 678. **So this case is not
blocked.** Score it against the artifact, not against `tasks` row 6.

**The harder branch.** There is no correlationId, because the reporter
pasted the *request*, not the response — exactly what D2 predicts. So
`FindRequestLog` cannot filter on an id, and the routine's step 3 falls
through to "approximately, by path and timestamp", then to replay. See the
open question.

#### The four answers, first run — 2026-09-21

Run against a copy of the live database, from `Resolve` onwards, with the
real `ssh dev` and **`Diagnose` switched off**: five nodes, 5.1 s wall,
**0 model calls**.

```
resolve            ok         5ms
find_request_log   empty   5123ms   kubectl holds nothing from 2026-09-19T22:41:02Z
read_failing_code  empty      0ms   no stack frame in the dossier
diagnose           skipped    0ms   no diagnose agent is configured
report             ok         5ms
```

**1. Did the dossier hold the decisive line? No — and the distillation rule
is not why.** The line is gone. The pod's oldest retained line at run time
was `2026-09-21T07:11:51Z`; the request was made `2026-09-20T04:41`, about
**27 hours earlier**. Two probes an hour apart saw two different oldest
lines, so this is not a retention window but the pod's last restart, and dev
pods restart often. The slice widened 30m → 6h as designed and neither
window could reach.

**2. Did `Diagnose` cite a real ref, and was the cause right?** Not asked.
This pass ran with the agent off on purpose — see "how to run this cheaply"
below.

**3. Numbers.** 0 dossier tokens and 0 model calls, because nothing was
found to reason over. `find_request_log` at 5.1 s is four SSH round trips
(pod lookup + logs, twice, for the widening) against ticket 16's measured
1.5 s each. Everything else is under 10 ms: this line is I/O and a model,
never compute.

**4. Which seam broke?** Not the pool, the outbox or the checkpoint. **A
source did**, and it failed in the worst available way: `kubectl --tail`
counts from the *newest* line and there is no `--until`, so a window opened
16 hours ago came back as the newest 400 lines of **today** — errors from
`/v1/funnelfox/execute-template`, another endpoint entirely — and the node
returned them as `ok`, 61 lines kept of 400. A diagnosis built on that would
have been confident, well-evidenced and about someone else's request.

Fixed the same day: `--timestamps` gives the container runtime's own stamp
(format-independent, unlike the line's `"time"` field, because dev is JSON
today and a Python traceback tomorrow), the source clips to the window, and
the node now tells **out of reach** apart from **not found** — "its oldest
line is X, so this request was not searched for; it is no longer there to
search".

#### What this case actually taught

**Dev keeps less than the reporter takes to report.** That is not a
detail — it is the branch. If a dev pod holds a few hours at best and a
report arrives a day later, `FindRequestLog` on dev will usually find
nothing, whatever the distillation rule does. Three consequences:

- **Ticket 16's measurement 2 (reporter delay) stops being owed and becomes
  decisive.** It decides whether the dev branch is worth building out.
- **The open question above — may Friday replay a request? — is now the main
  question on dev**, not a corner case. Replay may be the only thing that
  can answer a dev report at all.
- The production branch is unaffected: Loki keeps 30 days (ticket 16).

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

## Built 2026-09-20 — the code, not the runs

`friday/dag/api_issue/` exists again: six nodes, one module each, its own
prompt, and the one agent behind `Diagnose` built from its own declaration.
`api_issue` is no longer a one-node graph.

**Three layers, after the operator's reading of the first draft** (2026-09-20):
a **source** (`friday/sources/`) is a capability that reads one kind of thing
and decides nothing; a **check** is a formula over sources; a **node** is the
frame a run is checkpointed, timed and retried in. The first draft had the
two log back ends and the clone reader inside `api_issue/`, which is what
ticket 15 says must live in one package nothing else touches — they moved,
and `tests/test_sources_are_the_only_door.py` now holds the line. Nodes kept
their names: whatever a node returns is written to `dag_state` on every run,
so a node is a boundary rather than a unit of reuse, and reordering is
already a change to `edges` in one function.

**What each node does, and where it stops.**

- `resolve` — environment from the domain by rule in code (D1: `dev`,
  `production`, `external`); `route` → `service` rows for where it runs (D3).
  Hands over on an external domain, a missing row, a row whose `env`
  contradicts the domain, and on a report with no URL at all (D2: findable is
  not routable).
- `find_request_log` — `SshKubectlSource` for dev, `LokiSource` for
  production, both read-only. **The window is measured back from the
  reporter's message**, not from now (D5), which is what makes the slice
  runnable against five *past* cases at all: 30 minutes back, widened once to
  six hours when the first cut holds neither an error nor a stack. A source
  that is not configured **skips out loud**.
- `distil` — the recall-first rule as a pure function over lines, which is
  what lets ticket 16's coverage test reach it. Two bugs its own tests
  found: production log lines are JSON and spell it `"level":"error"`, so the
  match is case-insensitive; and a stack frame is decisive in its own right,
  not only when the line around it says ERROR.
- `read_failing_code` — frame → file in the operator's clone, at HEAD, and it
  says in `not_checked` that HEAD is not the running tag. A frame that
  resolves outside the clone is never opened: it arrives from a log line, and
  a log line carries whatever somebody got the service to print.
- `diagnose` — one model call over fixed evidence, no tools, answering
  `Diagnosis{cause, confidence (five rungs), conclusive, refs, next_checks}`.
  **The grounding gate**, and `refs` are **pointers, not quotes**: every line
  in the prompt carries an id, the model names ids, and code puts the text
  back. That is the spec's own measurement (ticket 16 — the configured model
  quotes a JSON log line right 32 times in 40 and points at one 20 times in
  20), and the first version of this node was built against the design that
  measurement replaced. A pointer that resolves to nothing voids the answer;
  so does calling an answer conclusive while pointing at nothing.
  `not_checked` is code-authored rather than asked of the model — the nodes
  already know what they skipped.
- `report` — writes `data/reports/<task>-<when>.md` and hands over. Nothing
  is sent to anybody.

**Configuration.** `api_issue:` in `config.yaml` (`ssh_host`, `loki_server`,
`loki_tool`, `reports_dir`) and an `agents.diagnose` block. Comment the agent
block out and the graph still runs: every node says what it could not do.

**Verified.** Whole suite `1325 passed, 1 skipped`. Seven guards deleted one
at a time and watched go red — the grounding gate, the clone-root check, the
route/domain disagreement, the recall-first cap, the `resolve` edge, the
one-widening rule, and the room-over-`*` precedence. The clone-root check was
**green** on its first mutation: the traversal in the test landed on a file
that did not exist, so `is_file()` was refusing it and not the guard. The
test now escapes to a file that is really there.

**What the review changed.** Two subagents read this before it was
committed. Eight real faults between them, all fixed here: the log window was
anchored to `now` rather than to the reporter's message (D5 — fatal for five
old cases); `refs` were verbatim quotes, the design the spec had already
retired on a measurement; stack frames were truncated to three *before*
`node_modules` was dropped, so a NestJS trace buried its own throw site; an
empty `repo_path` turned the clone-root check into "anywhere under the
process's working directory"; `ssh_host:` left blank in YAML became the
truthy string `"None"`; a killed SSH child was never reaped; the report file
ignored D10's `<task_id>.md`; and a comment promised configurable Loki
argument names that are hardcoded. Two of those were guards that had stopped
guarding while still looking like guards.

**Still divergent from the spec, on purpose and named here.**

- **`≤ 12 lines` of dossier is not reached.** The spec gets there with an
  error-code histogram — counts, not lines — which is ticket 05's. What this
  does instead is cap the loud lines belonging to *other* requests at 20 and
  report the rest as a count.
- **No `alternatives_rejected`** in the answer shape; the spec's build order
  names it. Ticket 05.
- **`ReadFailingCode` reads ±15 lines around the first frame** but not the
  enclosing function's name or one hop of callers — both need CodeGraph,
  which is ticket 04.
- **Answer 3 (dossier tokens, wall time per node, model calls) is not in the
  report.** It is in the database — `node_runs` per attempt, `model_calls`
  per call — so the five runs can still answer it; the report carries lines
  kept of lines offered.

**Not done, and not hidden.**

- **The five runs.** Cases 2–5 do not exist and case 1 has no cause. Nothing
  here has been run against a real cluster; every log source in the suite is
  a fake. The four questions this ticket asks are still unanswered.
- **`memories` is empty.** Until two `route` rows and one `service` row are
  typed in, every real run hands over on "no route row" — which is the
  designed behaviour, and also means the slice cannot be exercised yet.
- **The Loki branch has never been called.** `loki_tool` is a guess with a
  default; ticket 16 read the server's catalogue without running a query
  through it. The first production case will either work or name the tool it
  could not find.
- **The reporter's curl is still stored with its token** (ticket 17) and is
  still the model's retyping of it (ticket 18). Case 1 cannot be scored until
  18 lands.

---

## The first run against production (2026-09-21)

Not one of cases 1–5. Those are on dev, where the pod keeps only what it has
logged since its last restart, and case 1's request was 27 hours older than
the oldest line still there. Loki keeps thirty days, so the first real run is
a case found **in** production rather than one reported about it:

> `POST /v1/onboarding/completed` → 400, `ERR19`,
> `correlationId=b62ff26d-4b32-4617-a7c1-aa99e0509267`,
> 2026-09-21T10:35:01.899Z, `backend-reelme-v2` / `vsl` / `oregon-llm`.

### Answer 1 — did the dossier hold the decisive line? **No. Three reasons.**

Run with the node's own parameters — a 35-minute window, `limit=400`:

| asked | got |
| --- | --- |
| 10:10:00 → 10:45:00Z, 2,100 seconds | 400 lines spanning **84 seconds** (10:38:20–10:39:44) |
| the request at 10:35:01 | **not in it** |
| dossier | 4 lines of an unrelated minute |
| histogram | empty |
| `worth_widening` | `False` — the widening never fired |

**A. `limit` is a tail, not a sample.** The service emits ~5.9 lines/second,
so 400 lines is three per cent of the window, and Loki's default `backward`
direction makes that three per cent the *end* of it. This is the
`kubectl --tail` fault of the day before, reborn in the other source: the
lesson had been fixed in one place instead of being made a rule. It is one
now — `LogSource.lines` takes a `needle`, and *which* lines is part of the
read rather than a step after it. Filtering after the read cannot recover a
line the read never fetched.

**B. `WARN` counted as an error, so the widening was disarmed for ever.**
`backend-reelme-v2` emits ~7 WARN a minute of routine chatter ("Engine
returned an unmappable node status … skipping node", "No credit cost
configured … falling back to 15"). Nothing is wrong; that is the service
working. With WARN counting, `has_error` is true in *every* window this
service will ever produce, so `worth_widening` is false in every window too.
A chatty service switched off the spec's one automatic escape hatch silently.
`_LOUD` still decides what is kept — a warning is often the line before the
failure — and a new, narrower `_ERROR` decides whether anything failed.

**C. "A sample of the window" is not an honest enough sentence.** A dossier
drawn from the last 84 seconds of a 35-minute window reads exactly like a
dossier of the whole of it. It now names the span it covered, and says which
claims that limits: the counts and other requests' lines, never this
request's own, which come from a read narrowed to them.

**The fix, measured on the same case.** `{…} |= "<correlationId>"` returns
2 lines, `truncated: false`, containing both the `LoggerMiddleware` line and
the `ExceptionFilter` line with `error.message` and the request body. 174 KB
→ 2 KB; three per cent of a window → all of one request. Cheaper, not dearer.
Through the fixed path end to end: an 8-line dossier, `has_stack` true,
histogram `ERR19×1`, and the decisive line present.

### Answer 3 — sizes

Dossier 8 lines of 402 offered, inside the spec's `≤ 12`. Two Loki calls per
window instead of one; the narrowed one is the cheap one.

### Answer 4 — which seam broke

**The source, and only the source.** Not the pool, the outbox, the checkpoint
or the board. Both faults were a back end's bound being taken for the thing
it bounds — a cap read as a sample, a log level read as a verdict.

This case also answers a question the ticket had not asked. **Every frame in
its stack is in `node_modules`** (NestJS's `ValidationPipe`), so `NOT_OURS`
filters all of them and `ReadFailingCode` returns nothing. That is correct,
and it means the diagnosis has to be drawn from `error.message` in the log.
A graph that treated an empty code node as a failure would hand back the one
shape production produces most.

### Still owed

Cases 1–5 remain unanswered: they are dev cases and dev retention cannot
reach them. Answer 2 (did `Diagnose` cite a real ref, and was the cause
right?) needs a model call this run did not make.

### What the review changed (the A/B/C fix, 2026-09-21)

Six real faults, all fixed here.

- **The `grep` that narrows a `kubectl` read swallowed `kubectl`'s own
  failure.** A pipeline exits with its last command's status, so a pod that
  had gone, an RBAC denial or the wrong container all arrived as empty
  output and were reported as "the window holds nothing about this request"
  — the confusion between *not found* and *not searched* this node already
  existed to prevent. Now `bash -o pipefail -c`, with an empty `grep` match
  allowed explicitly as the one non-zero that is not a failure.
- **`kubectl` never set `truncated`, so fix C was dead on dev** — which is
  where every case this ticket has is logged. It says nothing about having
  cut, unlike Loki, so the cap is inferred: exactly `limit` lines back from
  a bound of `limit`.
- **"of the 30m asked for" was five minutes short.** The read spans
  `since → reported_at + MARGIN`. A sentence corrected for accuracy that is
  itself inaccurate is worse than the vague one it replaced.
- **The seam between the two reads was unmarked**, and in this very case it
  is a twenty-three minute jump. `distil` keeps a line either side of what
  it keeps, so it reached across and presented two unrelated spans as one
  story. Marked now, with a line carrying no error word, no error code and
  nothing shaped like a frame.
- **The capped sentence over-claimed the histogram**: the counts cover the
  window *and* this request's own lines, not the window alone.
- `_covered`'s docstring described a return value it does not have.

**And five guards that guarded nothing** — each found by deleting the code
and watching the suite stay green. This is the same fault as the one caught
on 2026-09-20 and it was made the same way: the mutation pass deleted what
was *believed* to be the guard rather than each new line. The span in the
capped sentence, `Lines.newest` in both parsers, the LogQL escaping *at its
call site* (the test only ever handed `_logql` a uuid), the `not ours.lines`
clause in the retention check, and `ours.truncated`, which had no test at
all and was unreachable on dev besides. One more was found the same way
after the review, while fixing it: the new `truncated` test called `_within`
directly and so made the inference it was meant to be testing.

Eleven mutations, eleven red. Suite `1449 passed, 1 skipped`.
