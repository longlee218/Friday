Type: grilling
Status: resolved
Blocked by:

# How the verifier agent is trusted

## Question

`acceptanceCriteria` is checked by a **separate agent** (charting Q2), not the
doer — the antidote to "self-feels done". But an LLM verifier has its own
"feels done" risk, and the repo's own rule (`evals/api_issue.py`) is blunt: *"an
LLM critic is an eval variant until its scores agree with the operator's
marks."* So the verifier is not trustworthy until calibrated.

Decide:

- **The code/model split**: which criteria are checked by **deterministic code**
  (refs resolve, `conclusive ⇒ ≥1 alternative`, a test exit code later) vs by a
  **model judge** (does the cause actually match the evidence)? Push everything
  cheap into code; the model judge is the last resort.
- **Calibration**: how the verifier's verdicts are measured against operator
  marks before they gate anything — reuse the cassette-eval + `cause_mentions`
  labels from `build-the-loop` tickets 1/2? What agreement bar counts as
  calibrated?
- **What a failed verdict does**: re-plan (bounded), `HandOver`, or `Ask`? And
  does the operator see the verifier's reasoning on the review summary?
- **Independence**: the verifier gets a fresh context and the artifact only
  (not the doer's chain), so it re-derives rather than rubber-stamps — like the
  code-review subagent that caught the ticket-2 fall-through.

Independent of the schema; can be worked in parallel with ticket 01.

## Answer

Decided 2026-09-26. Confirmed against a survey of how large agent systems verify
output (OpenHands, AutoGPT, Voyager, Reflexion, SWE-bench, G-Eval / LLM-as-judge).

**Framing correction.** Verification is not "does the artifact exist / cite a
line" — that is only a floor. The load-bearing question is *"is the Diagnose
result actually correct?"*, which is semantic. Friday's diagnose output is
**read-only, non-executable** — there is no test-suite ground truth (the
SWE-bench mechanism). So an **agent Judge is required**, not optional. Two tiers:

1. **Grounding gate (`check="code"`) — the floor.** Every claim must trace to a
   real cited line (`Lnn`), `conclusive ⇒ ≥1 alternative`, `not_checked` stated.
   Cheap, deterministic, needs no calibration. This is also the antidote to a
   Judge being **gamed by verbose, citation-stuffed text** (the documented
   LLM-judge failure mode) — the citations must actually resolve.
2. **Judge (`check="agent"`) — the load-bearing verdict.** Scores *correctness*:
   does the cause match the evidence, are alternatives weighed, any unsupported
   leaps. This is the antidote to "self-feels done".

**Q3.1 — Code vs agent split.** Not "pick one." Both tiers, always. Code owns
traceability (existence/resolution predicates); the agent Judge owns the
semantic correctness verdict. Do **not** try to reduce correctness to code
checks — that was the mistake this ticket corrects.

**Q3.2 — How the Judge is trusted (calibration).** Per the repo's LLM-critic
rule:
- **Rubric, not "is it good?"** — the Judge scores concrete dimensions
  (grounding, causal completeness, alternatives considered, no unsupported
  leaps), G-Eval style. Rubric-scoring beats bare intelligence and is auditable.
- **Shadow first.** The Judge runs advisory-only on the `build-the-loop`
  cassette cases; the operator marks the same cases; verdicts are compared. It
  **gates nothing** while shadowing.
- **Gate only after ≥10 labeled cases** on which Judge agrees with the operator.
  **Asymmetric bar:** a false-"pass" (blessing a wrong diagnosis) is far worse
  than a false-"fail"; tune the Judge to err strict.

**Q3.3 — A failed verdict → `HandOver`.** Read-only today (no replan engine), so
a Judge FAIL stops and hands the case to the operator, naming **exactly which
criteria failed** plus the Judge's reasoning on the review summary. `Ask` is for
missing reporter *input*, not for a self-check failure. Never silently downgrade
to "done". When durable-spine's replan lands, insert a **bounded replan** (retry
N times) *before* HandOver.

**Q3.4 — Independence.** The Judge gets a **fresh context** and **only the
artifact**: objective + acceptanceCriteria (rubric) + the Diagnosis + the cited
evidence lines (`Lnn`). It does **not** see the doer's chain-of-thought — seeing
the doer's reasoning is how self-critique rubber-stamps (AutoGPT's failure). It
re-derives, like the `code-review` subagent that caught the ticket-2
fall-through.

**Consequences for the schema (ticket 02).** `Acceptance.check` stays
`"code" | "agent"`. Most api_issue grounding criteria are `check="code"` and land
now; the correctness Judge is `check="agent"`, built as a separate agent, shipped
**shadow-only** until calibrated. The Judge's rubric is the per-type
`acceptance_template`'s `check="agent"` entries. `on_obstacle="handover"` is the
Judge-FAIL path today; `"replan"` waits for durable-spine.
