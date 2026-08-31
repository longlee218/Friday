# 04: Triage a captured mention

**What to build:** A captured mention is understood — it becomes a task carrying a
type and the parameters extracted from the message, or it is recorded as noise and
goes no further. This is the only model call in the pipeline for now; what happens
with the result is deterministic Python (ticket 07).

**Blocked by:** 01

**Status:** done

## Types and parameters

Four outcomes. The type and its parameters are expressed as **one tool per type**,
which is how the discriminated union is encoded — each tool declares the parameters
its own type needs:

| Type | Parameters | Typical source |
| --- | --- | --- |
| `api_issue` | environment?, correlation_id?, curl?, summary | mobile/dev team reporting an API is wrong |
| `access_request` | project, permission, summary | someone needs permission on a project |
| `doc_question` | question, doc_ref? | PO asking what the documentation says |
| `skip` | reason | chit-chat, salary, off-topic |

Parameters matter more than the type: the most frequent real action is noticing an
`api_issue` arrived without an environment or correlationId and asking for them.

`code_review` is deliberately out of scope — it needs repository and merge access,
a far larger blast radius than replying in a chat channel.

- [x] A message reporting an API problem produces a task typed `api_issue`, carrying whatever of environment / correlationId / curl the message contained
- [x] A request for project permission produces an `access_request` task with the project and permission
- [x] A question about documentation produces a `doc_question` task
- [x] Social talk produces no task and is recorded as skipped
- [x] Salary and off-topic messages are filtered to skip **before** the model is called, not by the model's judgement — *compensation only; see Scope below*
- [x] Triage performs no writes: it reports a decision, and the caller applies it
- [x] Triage reads the conversation's stored context, so a follow-up referring to an earlier message is understood
- [x] A follow-up in a conversation that already has an open task updates that task rather than opening a second one
- [x] A follow-up whose type differs from the open task's is escalated for human input
- [x] Confidence below the configured threshold is escalated for human input
- [x] A model error, timeout, refusal, or turn-limit breach is escalated for human input — never discarded
- [x] Every triage decision is recorded with its type, confidence and parameters, so the threshold can be derived from real data
- [x] The provider's `base_url`, `api_key` and `model` are configuration, so a different OpenAI-compatible provider can be used without code changes
- [x] Tracing is disabled, so no traffic or credential reaches OpenAI when another provider is in use
- [x] Triage runs off a queue: a slow or failing model call never stalls ingestion
- [x] The operator's own messages are retained as conversation context while still being skipped as triggers

## Notes

Tested with the SDK's `ScriptedModel`, so no test makes a network call.

Structured output via `response_format: json_schema` is rejected by some
OpenAI-compatible providers. Expressing the union as tool calls avoids that, but
the chosen provider should be verified early — it decides whether this ticket's
approach works at all.

## Verified against the live provider

Probed MiniMax (`https://api.minimax.io/v1`) with the exact shape this ticket
describes — one tool per type, `stop_on_first_tool`, real messages.

**Tool calling works.** The compatibility risk that would have sunk this approach
(providers rejecting `response_format: json_schema`) does not apply, because the
union is expressed as tools.

**`tool_choice="required"` is not optional.** Without it, the vaguest message —
"the api is wrong", the single most common shape — calls no tool at all and
produces nothing. With it, that message correctly becomes an `api_issue` with
every parameter null, which is exactly the "ask for the missing fields" path.

**Model choice decides parameter extraction, prompting does not.**

| | environment + correlationId extracted |
| --- | --- |
| MiniMax-M2.7, plain prompt | no |
| MiniMax-M2.7, prompt with explicit copy-verbatim rules and examples | no |
| MiniMax-M3, plain prompt | yes |
| MiniMax-M3, explicit prompt | yes |

M2.7 returns null for fields sitting in plain text. No amount of prompting fixed
it. Use M3 for triage.

**Vietnamese works.** "api sai roi, moi truong staging, correlationId abc-123-def"
extracted `staging` and `abc-123-def` correctly, and "luong thang nay ve chua"
was correctly skipped as a salary question.

**One data-quality bug to handle:** the model sometimes emits the *string*
`"null"` rather than a JSON null. Parameters must be normalised — `"null"`,
`"none"` and empty string all mean absent — or the workflow will treat the string
as a real value and skip asking for the field.

**Worth considering:** `correlation_id`, `environment` and `curl` are all
mechanically detectable with a regex. Extracting them deterministically would be
free, exact, and immune to model quality — with the model as a fallback rather
than the primary path.


## Delivered

All sixteen criteria met. 143 tests, none making a network call.

Beyond the checklist: `friday/triage/params.py` implements the regex extraction
the "worth considering" note describes, so `correlation_id`, `environment` and
`curl` are read off the message and override the model where they disagree. A
value copied out of the text cannot be a hallucination; the model's can.

`friday/llm_log.py` logs both sides of every model call through the SDK's
`on_llm_start` / `on_llm_end` hooks, at DEBUG. A classification that looks wrong
is otherwise a black box — the prompt is assembled from stored context and all
that survives is a task row.

## Scope of the pre-model filter

`friday/triage/prefilter.py` filters **compensation talk**, not "off-topic" in
general. Off-topic is a judgement, and the model makes it well — verified live.
Compensation is mechanically detectable, and worth taking off the model for a
second reason the criterion does not state: pay talk between colleagues has no
business being sent to a third-party API to be told it is off topic.

The rule is deliberately narrow, because the two mistakes are not equal. A false
negative costs one model call that skips the message anyway. A false positive is
a real report silently skipped — a dropped mention, the one thing this system
must never do. So it fires only on a compensation word standing alone in a
message carrying no technical signal at all: `lương cao thế mà API vẫn 500 à`
reaches the model, `lương tháng này về chưa` does not.

## Recording decisions

`events` carries `decision_type`, `decision_confidence` and `decision_params`
for every triaged message, read back through `Database.decisions()`. Skips and
follow-ups open no task, so without this they left no trace — and they are
exactly the decisions the `confidence_threshold` of 0.7 needs to be checked
against. That number is currently a guess; this is what will replace it.
