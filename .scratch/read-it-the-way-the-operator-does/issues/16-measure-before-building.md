# 16: Measure before building

**What to do:** Replace the spec's estimates with numbers, and probe the
configured model before a line of the Collector is written.

**Blocked by:** nothing. The devops MCP has to be authenticated by the
operator for the first three.

**Decisions:** the operator's 2026-09-18 call; the spec's "Diagnose's
context" carries the numbers this ticket must confirm or replace.

**Status:** done 2026-09-18, except measurement 2 (reporter delay), whose
stated supplier has fallen through (2026-09-22). This said ticket 00's five
cases would supply it; those cases cannot be run at all, because dev
retention does not reach back to them. Two probes an hour apart on
2026-09-21 saw different oldest lines, and case 1's request was 27 hours
older than the oldest line its pod still held — which is itself the
measurement's most useful number so far, and an argument that the reporter
delay matters more on dev than the window size does. A real figure now has
to come from captured production cases as they accumulate.

**Two of the numbers above rest on a live session and on nothing in this
repository** — the two probes an hour apart, and case 1's request being 27
hours older than its pod's oldest line. They were read off a cluster that
has since moved on, and no transcript of them was kept. Treated as
indicative, not as measurements, until a captured case carries the same
shape; the same goes for the 2026-09-21 reading that production ran image
tag `0.4.4`, which needs the devops MCP to check again.

## Measurements

1. **Dossier size.** Pull 20 real failed requests of `backend-reelme-v2`
   from Loki (status ≥ 400 or `ExceptionFilter`), apply the recall-first
   distillation offline, count characters ÷ 4. Sets `dossier_budget_tokens`.
2. **Reporter delay.** For every past `api_issue` task with a findable
   request, the gap between the reporter's message time and the request's
   timestamp. Sets the search window (currently "6 h", a guess).
3. **Source latency.** Time each primitive — Loki query, kubectl logs,
   codegraph explore, file read — ten times each. Sets the per-check and
   per-graph ceilings (currently "5 min", a guess).
4. **Error-code frequency** in Loki over 30 days, per service. Says which
   runbooks are worth writing first (ticket 07).
5. **Seed rows.** From Loki `app` labels, the eight repo names and domain
   probing, draft the `service` and `route` rows for the operator to
   confirm rather than type.

## Measured so far

- **2, reporter delay — cannot be measured from Friday's database.** Read
  2026-09-18: 26 messages in total (2026-09-01 to 2026-09-14), **one**
  `api_issue` task ever, with a summary and no curl, response or
  correlationId. n = 1 and no request timestamp. The window has to come
  from the five cases the operator picks for ticket 00: for each, the
  message time and the request's Loki timestamp, by hand. Until then "6 h"
  stays an estimate and the slice logs the actual gap it finds.

- **1, dossier size — measured 2026-09-18 on production `backend-reelme-v2`,
  15 failed requests across 5 error codes.** A failed request has exactly
  **two lines of its own**: one `LoggerMiddleware` INFO and one
  `ExceptionFilter` ERROR; no `BusinessEvent` line in any of the 15. Raw,
  those two lines are ~1,100 chars ≈ **275 tokens**. Distilled by rule
  (request fields · exception class and message · in-app frames only, 1–3 of
  them, median trace 273 chars · error-code histogram of the ±5 min window ·
  same-user errors capped at 8) the log part of the dossier is **~170
  tokens median, 360 max**. So the whole dossier is dominated by code
  context, not logs: ±15 lines per frame ≈ 500 tokens. A 5,000-token
  dossier budget is ~4× what an easy case needs; the budget can go to code
  (more lines, two hops of callers) rather than be spent.
- **The "every ERROR line within ±5 min" rule is refuted.** The service logs
  **~1,000 exceptions an hour** (1,000 lines covered 23:02–23:59 on
  2026-09-17); ±5 min around a sample request holds a **median of 58 ERROR
  lines ≈ 12,000 tokens**, up to 885, and one user alone produced 880 of them
  in five minutes (`ERR951`/`ERR955` on `/v1/pod/previews`, a polling loop).
  Replaced by: a **histogram** (counts per error code, 3–4 codes ≈ 60
  chars), plus same-user and same-path lines **capped at 8** and reported as
  "8 of N". Raw stays behind the ref.
- **4, error-code frequency — measured 2026-09-18, 30 days, production
  `backend-reelme-v2`, 90,327 exceptions.** By status: 404 63,962 · 429
  8,683 · 400 7,621 · 401 3,987 · 403 3,456 · **500 2,104** · 409 488 · 503
  23 · 502 3. By code: `ERR951` 49,663 and `ERR955` 8,664 (POD preview
  polling — noise, worth a `fact` row saying so, not a runbook); `ERR19`
  22,543, and **every one of the 2,104 HTTP 500s is `ERR19`** (the generic
  code — the first runbook, since a reporter's "500" is always this);
  the Midas family `ERR306` 3,455 · `ERR303` 2,396 · `ERR302` 220 ·
  `ERR300` 81 · `ERR301` 10 — the second runbook, the operator's own
  example; `ERR943` 1,896 · `ERR24` 834 · `ERR500` 173 · `ERR16` 118.
  Everything else is under 100 a month.
- **Seen in passing:** log bodies carry reporter emails and the app already
  writes `"refreshToken":"[REDACTED]"` itself; D11 (no redaction while the
  agent thinks) stands, and finding C (strip the curl's own token) is still
  needed because the *curl* is not an app log.

- **3, Source latency, dev — measured 2026-09-18 from the operator's Mac.**
  Dev is reached by `ssh dev` (alias in `~/.ssh/config`, non-interactive
  with the agent loaded) and `kubectl` runs **on that host**, not locally —
  the spec's "kubeconfig on this machine" was wrong. Round trips, each
  including the SSH handshake: `get pods -A` 1.5 s; `logs --tail=200`
  (110 KB) 1.2 s; `logs --since=6h` (9,143 lines: 436 `ExceptionFilter`,
  6,540 `LoggerMiddleware`) 1.5 s. Namespace `dev`; pods `backend-reelme-v2`,
  `backend-reelme`, `ai-backend-reelme-payment`, `backend-reelme-template-cms`.
  **The dev log format is identical to production** (same JSON fields, same
  `ExceptionFilter`/`LoggerMiddleware` contexts), so one distillation rule
  serves both. Limits found: `kubectl logs` holds only the current
  container's output — `backend-reelme` had restarted 49 times, `-v2` was
  128 min old — so dev history is bounded by the pod's last restart and
  `--previous` reaches one container back; grep must run **on the host**
  (`| grep <cid>`) so only the matching lines cross the wire.
- **Prod Loki latency:** each `loki_query_range` in this session answered in
  well under a second for ≤ 1,000 lines (not timed precisely; the 1,000-line
  pull was ~1 MB).

- **5, seed rows — drafted 2026-09-18** in `research/03-seed-rows.md`: 8
  `project`, 9 `service`, 19 `route`, 1 `dependency`, 3 `fact` rows, from
  the dev cluster's VirtualServices, production Loki labels and DNS. Open
  cells answered by the operator the same day: Midas = `ai-backend-reelme-
  payment`; the funnel's production app = `funnel-monkey`; stacks are mostly
  Python and Node.js, some Go — so `ReadFailingCode` needs a frame mapper per
  stack, chosen by `project.stack`. Also found: the domain rule has exceptions
  (`api-mobile-spec-reviewer.aperogroup.ai` is dev; `payment-service` has
  two production domains on different endpoints) and a `stg` namespace
  exists for one service outside the operator's projects.

## Model probe, half a day, three scripts

Run against the configured provider through the harness, the way the
tool-call-vs-`json_schema` probe was run:
- (a) given 30 candidate refs, answer with one that exists — 20 trials;
- (b) quote one line verbatim from a 2,000-token tool output — 20 trials;
- (c) complete a three-tool loop and finish through the answer tool.
Report pass rates. Below 90% on (a) or (b): `Diagnose` moves to a stronger
model in `config.yaml` (per-agent model already exists) or the Collector
ships with `max_collects_per_run = 0`; the ticket says which and why.

## Coverage test

For each labelled case, the operator names the decisive line by ref; a
suite test runs the distillation rule over the frozen raw data and asserts
that ref is in the dossier. Pure code, no model; it is the guard on the
rule that scored 4/10.

## Done when

Every number in the spec's context section carries "measured YYYY-MM-DD on
…" or is deleted, and the probe's three rates are written here.

## Probe results — 2026-09-18, MiniMax-M3 through `Harness`, 308 s

Scripts: `research/probes/probe_model.py` and `probe_pointer.py`, run with `uv run python` from the repo root (synthetic dossiers and log lines shaped
like the real ones measured above).

| Probe | Result | Bar | Verdict |
|---|---|---|---|
| (a) pick the one decisive ref among 30 | grounded **19/20**, right **19/20** (95%) | 90% | **pass** |
| (b) quote a log line verbatim from ~2,000 tokens | **15/20** (75%) | 90% | **fail** |
| (c) finish a three-tool loop through the answer tool | finished **10/10**; called all three tools **8/10** | — | pass, with a caveat |

**(a)** The one miss put the claim text and the ref into the `ref` field
together — the ref inside was right. The answer tool's ref-check refuses it
and the model gets one correction turn, so in the real harness this case is
recovered, not lost.

**(b) fails, and the way it fails matters.** Every failure is the same one:
asked to quote a JSON log line, the model answered with **the line's own
JSON keys as the answer's arguments** (`context, correlationId, level, msg,
time, trace`) instead of putting the line in `quote`. It never paraphrased —
all 15 successes were byte-exact. So the defect is structural (a JSON string
inside a JSON argument), not a model that rewrites evidence. **Consequence
for the design: the Collector does not copy lines; it points at them.** Its
answer carries a ref (`loki:<correlationId>:<ts>`, `file:<path>:<line>`) and
**code** fetches the verbatim text from the tool output it already holds.
The verbatim guarantee then costs nothing and cannot fail. Probe (b') tests
pointing instead of copying; result below.

**(c)** Always finished with correct refs. Two runs skipped `read_source`
and concluded from log + database; four runs repeated `search_logs` 3–6
times. So the turn ceiling is load-bearing (5 per collect stays), and "did
it read the code" is a coverage item for the supervisor, not something the
model can be trusted to do unasked.

### Second run and the pointer probe — 2026-09-18, 313 s

`probe_b2.py` imported `probe_model.py`, whose module-level `asyncio.run`
re-ran (a), (b) and (c) before (b'). Unplanned, and useful: every probe now
has two independent runs.

| Probe | Run 1 | Run 2 | Combined | Bar |
|---|---|---|---|---|
| (a) pick the decisive ref among 30 | 19/20 | 18/20 | **37/40 = 92.5%** | 90% — pass |
| (b) quote a log line verbatim | 15/20 | 17/20 | **32/40 = 80%** | 90% — fail |
| **(b') point at the line by its correlationId** | — | **20/20** | **100%** | 90% — pass |
| (c) finish the three-tool loop | 10/10 | 9/10 | **19/20 = 95%** | pass |
| (c) called all three tools | 8/10 | 10/10 | **18/20 = 90%** | — |

All three (a) misses have one shape: claim text and ref written together
into `ref`, with the correct ref inside. The ref-check refuses it and the
correction turn recovers it. All eight (b) failures have one shape too: the
quoted line's own JSON keys returned as the answer's arguments. No
paraphrase in 32 successes. The one (c) failure (run 2, c02) called all
three tools and then never produced the answer.

**Decided by the numbers:** the Collector answers with **pointers** —
`correlation_id`, `file:line`, `db:<check>:<key>` — and code fetches the
verbatim text from the tool output it holds. `claims[].quote` is filled by
code, never by the model. The spec's "code checks the quote occurs in a
tool output" becomes unnecessary; a pointer either resolves or is refused.
MiniMax-M3 stays on `Diagnose` and the Collector; no stronger model needed.
