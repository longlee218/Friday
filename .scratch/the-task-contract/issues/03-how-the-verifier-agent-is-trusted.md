Type: grilling
Status: open
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
