Type: prototype
Status: resolved
Blocked by: 01

# What Intake gathers, and the shape the loop returns

## Question

Specify concretely — as stubs to react to, not a build:

1. **Intake's output.** The exact metadata it gathers, all deterministic:
   placement (env / service / clone), repo path, cluster / namespace /
   pod-selector, db list, error-code doc path, release tag — plus the
   deterministic **memory / skill retrieval** (which memory kinds, how a
   skill's `when:` matches, the token budget). No LLM call (charting Q8).
   `file that failed` / `pod that died` are **not** here — they are discovered
   by the loop from a stack frame.

2. **The Diagnose loop's structured output type** — `Diagnosis | Ask |
   HandOver` — the fields of each, consistent with the resume decision from
   [The reply that both resumes and invalidates](01-the-reply-that-both-resumes-and-invalidates.md):
   what an `Ask` carries (question, what's missing), what a `HandOver` carries,
   and what the resume payload looks like when the reporter answers.

Blocked by ticket 01 because the output schema (Ask/HandOver + resume payload)
depends on how resume-vs-invalidate is decided. Intake's contents can be
sketched independently, but the ticket lands both together once 01 is settled.

## Answer

Decided 2026-09-24 (prototype). Stub to react to:
[`intake_and_loop_output_STUB.py`](../intake_and_loop_output_STUB.py) (throwaway;
fold the shapes into real code at build, delete the stub).

**Intake gathers (deterministic, no LLM):**
- `Placement` — `env` (domain table), `service`, `clone_path`, `repo_path`,
  `release_tag`, `namespace`, `pod_selector`, `dbs`, `error_code_doc`, and
  **`container_roots`** (added — `read_code` needs it to map compiled frames).
- `Hints` — cheap regex/artifact-id pulls, no model: `correlation_id` (uuid),
  `curl_artifact_id`, `response_artifact_id`. The rich reading (endpoint / what
  is wrong) is the loop's job.
- retrieved, token-bounded, deterministic: `memory` (fact/constraint/decision/
  finding), `skills` (`when:`-matched), `related_tasks`.
- `request_text` (the turn, passed to the loop) and **`reported_at`** (added —
  `read_log`'s window is measured back from it).
- `placement_identity = (env, service, clone_path, repo_path, release_tag)` —
  the checkpoint discard key (ticket 01); memory/skills changing does not
  invalidate a running investigation.

**Loop output = `Diagnosis | Ask | HandOver`** (reuses Friday's 3 Actions):
- `Diagnosis` (done → Report/Reply): unchanged from today (`cause`,
  `confidence`, `conclusive`, `refs` `Lnn`, `next_checks`, `alternatives`).
- `Ask` (need reporter): `question` + `missing`; → Action=Ask, pool pauses,
  reply resumes from `message_history` iff `placement_identity` unchanged.
- `HandOver` (escalate): `reason` + `found_so_far`; operator only.

**Service-resolution fork — decided (c)→(a) hybrid:** Intake string-matches the
request against the known service list / aliases (no model); if vague or none,
it yields the **room's candidate set** and the loop picks from that set by
reading. The model never invents a service — it only **selects** from a
deterministic candidate set, so "service is never a model guess" holds while
staying flexible.

**Not added (over-fetch / out of scope):** dependency map (the loop resolves a
downstream service via the same fork if needed), plan-exemplars / similar cases
(learning loop), budget/MAX_READS (harness/config), resume marker (pool state +
`message_history`, not an Intake field). `log_sources` (loki/kubectl) is boot
config via deps, not Intake output.
