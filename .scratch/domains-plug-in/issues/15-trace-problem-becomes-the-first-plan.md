Type: prototype
Status: resolved
Blocked by: 10

# `trace_problem`'s graph becomes the first plan

## Question

Port the built graph `intake → acknowledge → diagnose loop → report` into the
main-flow `Plan` as the first worked example: phase 1 = agent `backend.diagnose`
with `backend.logs` + `backend.code` (its `ask_reporter` / `hand_over` terminal
tools map to `ask` / `hand_over`), phase 2 = `draft` report. Decide whether
acknowledge is a phase or a spine concern, what the plan's hypotheses and
done-criteria look like for a real case (the captured `prod-onboarding-400`),
and whether this plan is the Planner's exemplar / fallback. If it is awkward to
write, the vocabulary is wrong.

## Answer

Decided 2026-09-28 (prototype + grilling). Prototype: branch
`prototype/trace-problem-first-plan`, file
`.scratch/domains-plug-in/trace_problem_first_plan_STUB.py` — run it to see
acknowledge written both ways and four cases walked through the spine.

```
spine    Intake → acknowledge (spine, once) → Planner → GatePlan → Run → Draft
plan v1  p1 agent backend.diagnose [backend.logs, backend.code]
            "Find the 400 for the correlationId; read the validator at the
             running tag; say which field or rule rejects it."
         p2 draft reads p1
```

1. **Acknowledge is a spine concern, not a step.** Core queues it through
   the outbox once per task (a reply re-run of Intake never sends a second
   one), after Intake and before the Planner, without approval. As a step it
   needed a 5th type, waited on the Planner — the silence it exists to fill —
   could be forgotten or reworded, needed a GatePlan rule, and vanished on
   `planner_failed`. The step vocabulary stays `agent / ask / hand_over /
   draft`.
2. **The hook sits on the `Action`**: optional `acknowledge(IntakeContext) ->
   str | None`, outside the contract like `recognition` and `planning`.
   Absent or `None` → no acknowledgement (`ops.request_permission`). The text
   is about what the action does, so it belongs to the action, not the
   domain: `trace_problem` keeps today's `ack_text` ("đang xem log của
   <service>…", generic when the service is unresolved); `answer_question`
   writes its own (ticket 06).
3. **The report file goes**: `data/reports/*.md` and `DevopsConfig.reports_dir`
   are deleted. The board's stored plan versions and step results plus the
   `draft` step's operator brief replace it; nothing but its own tests read
   it.
4. **The vocabulary holds.** The graph ports as `p1 agent → p2 draft`; the
   agent's terminal tools `ask_reporter` / `hand_over` / `replan` give `Ask`
   / `HandOver` / `Replan` and stop the plan there. Four cases written
   without strain: happy path (`Diagnosis` → draft), agent asks, a vague
   request the Planner answers with `p1 ask`, and an unresolved service
   (the brief points the agent at the candidates in the intake context).
   Accepted oddity: on a vague request the reporter gets "đang xử lý…" and
   then the question.
5. **Not an exemplar, not a fallback.** No fallback plan (ticket 12);
   hypotheses and done-criteria are gone (ticket 10). Stored exemplars stay
   in the map's fog, "Getting smarter".

Carried: to **03** — `Action` gains the optional `acknowledge` hook. To
**14** — the acknowledgement is a spine step between Intake and the
Planner, once per task; a reply's Intake re-run does not send it again.
To **06** — `answer_question` decides its own acknowledgement text (or
none).
