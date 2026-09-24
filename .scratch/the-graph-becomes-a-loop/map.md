---
labels: wayfinder:map
---

# The graph becomes a loop

## Destination

Lock the target **shape** of the `devops.api_issue` graph — a decision, not a
build: collapse the 7 fixed nodes down to

```
Intake → [Acknowledge] → Diagnose (one bounded agent loop) → Report
```

with every open decision resolved, so a build board can be planned with nothing
left to decide. The map is done when the shape is fully decided and only
execution remains.

Scope: the `api_issue` graph only. See **Out of scope** for what this effort
deliberately does not touch.

## Notes

- **Domain**: Friday. Read `docs/DESIGN.md` § What exists, and `CONTEXT.md`
  § Graph / § Action / § Extraction, before deciding. This effort is the first
  concrete step *toward* — but is **not** — the
  `docs/research/durable-spine-dynamic-plans.md` direction; the `Intake` node
  here is that document's `Intake`.
- **Skills every session consults**: `grilling` + `domain-modeling` (this repo's
  wayfinder default).
- **Standing preferences**: design discussion in **Vietnamese**, code and docs
  in **English**. The grounding gate — `Diagnose`'s Evidence line-ids (`Lnn`),
  a ref that resolves to nothing voids the answer — is **inviolable** in every
  decision here.
- **Plan, don't do**: produce the decided shape. The build/migration is a
  separate board, listed under Not yet specified.

## Decisions so far

<!-- Settled in the charting conversation (2026-09-24). One line each; the
detail a ticket would hold lives in the ticket once one exists. -->

- **Drop `extraction`/`prepare` as a model-node** for api_issue — understanding
  the request is the loop's job.
- **One `Intake` node** replaces Resolve + node-0: static, read-only,
  **deterministic** metadata; Resolve folds in with env/service kept as a
  **table lookup, never a model guess**; Intake calls **no LLM**.
- **Remove `FindRequestLog` + `ReadFailingCode` as fixed nodes** — they become
  **tools of Diagnose** (the existing `diagnose_reads` loop).
- **Diagnose is one agent loop** with the grounding gate intact; **no
  sub-agents** now (shelved — threatens grounding, only pays for parallel
  hypotheses).
- **Findability is not a gate**: no upfront "correlationId/curl or Ask" check;
  the loop investigates and only `Ask`s when genuinely stuck. **This reverses
  `CONTEXT.md` § Action** — to be corrected there (and a possible ADR) at build
  time.
- **Loop stop conditions reuse Friday's 3 Actions**: `Reply` = done, `Ask` =
  need reporter, `HandOver` = escalate to operator. No new vocabulary.
- **Delete the fixed-feed baseline** (`diagnose_reads=False`) — **gated** on the
  cassette-eval proving agentic ≥ baseline on the 5 frozen cases.
- **Measure the loop by cassette**: record tool-call + response, replay
  deterministically, score cause/refs on the transcript.
- **Ask/HandOver are loop-boundary exits** reusing pool pause/resume; on resume,
  **continue** from the persisted agent `message_history` + `Evidence` (never
  restart — restarting re-burns reads and breaks `Lnn`); **no DBOS** yet
  (in-memory resume via the checkpoint).
- **Intake replaces `prepare` as the staleness anchor**: runs fresh each pass,
  never checkpointed, its output is the discard key; the `Extraction mark` is
  dropped (Intake makes no model call to memoize).
- **FACT** (research, 2026-09-24, Context7 `/pydantic/pydantic-ai` main,
  ≤ v2.0.0): Pydantic AI has first-class HITL — a run **ends** at a
  `DeferredToolRequests` boundary and resumes via `message_history` +
  `DeferredToolResults`; works **in-memory**, DBOS/Temporal only for crash
  recovery. So Ask/HandOver-at-boundary needs no new machinery.
- [The reply that both resumes and invalidates](issues/01-the-reply-that-both-resumes-and-invalidates.md):
  resume-vs-invalidate is decided **only** by diffing a **placement identity**
  (`env + service + clone + repo_path + release_tag`) taken from Intake —
  unchanged → the Diagnose checkpoint (`message_history` + `Evidence`) survives
  and the loop resumes with the reply appended as a reporter turn; changed →
  discard + re-investigate. No reply-intent classifier, no separate
  change-detector; the mixed case falls out of the rule. `placement identity`
  becomes the checkpoint discard key (replaces node-0 output as the anchor).
- [A deterministic eval for a loop that reads what it likes](issues/02-a-deterministic-eval-for-a-loop-that-reads-what-it-likes.md):
  the eval infra mostly exists (`evals/api_issue.py` scores the Diagnosis
  output, agnostic to how reads happened). The one break under agentic reads is
  the **canned source** — redefine a captured case's `reads` to hold the **raw
  incident-window superset** (+ a repo snapshot at the release tag + the error
  table) and **filter it in-process**, so the loop's arbitrary needles/windows
  replay faithfully; out-of-superset reads return the truthful out-of-reach /
  not-found answer. Scoring stays **deterministic substring on `cause_mentions`,
  no judge**; `evals/api_issue.py` unchanged. Deleting the fixed-feed baseline
  is gated on **case count** (≥ ~10, operator-captured), not the mechanism.
- [What Intake gathers, and the shape the loop returns](issues/03-what-intake-gathers-and-the-shape-the-loop-returns.md):
  `IntakeContext` = `Placement` (env/service/clone/repo/tag/ns/pod/dbs/
  error_doc/**container_roots**) + `Hints` (regex `correlation_id` + curl/
  response artifact ids, no model) + retrieved `memory`/`skills`/`related_tasks`
  + `request_text` + **`reported_at`**; `placement_identity` is the discard key.
  Loop output = `Diagnosis | Ask | HandOver`. **Service fork = (c)→(a) hybrid**:
  Intake matches known services; vague → room candidate set, the loop selects by
  reading — the model never invents a service. Stub:
  `intake_and_loop_output_STUB.py`.

## Not yet specified

- Whether `Report` / the brief changes at all under the new shape (likely
  unchanged: still a `Reply` that waits for approval) — confirm once the shape
  is locked.
- `Acknowledge`'s exact trigger and text under the new shape.
- The **build/migration board** — sequencing the decided rework into executable
  tickets, suite green at each step. A separate effort once this map is locked.
  Includes the **operator's case-capture work** (≥ ~10 captured cases) that
  gates deleting the fixed-feed baseline — execution, not a decision, so it
  lives there, not as a ticket on this map.

## Out of scope

- The universal durable spine / plan-as-data
  (`docs/research/durable-spine-dynamic-plans.md`) — a later effort.
- Sub-agents inside `Diagnose`.
- Changes to the shared `prepare_node` or to other task types (`access_request`,
  `doc_question`).
- The learning loop / plan-exemplar library / reflection node.
