# 04: Triage a captured mention

**What to build:** A captured mention is understood — it becomes a task carrying a
type and the parameters extracted from the message, or it is recorded as noise and
goes no further. This is the only model call in the pipeline for now; what happens
with the result is deterministic Python (ticket 07).

**Blocked by:** 01

**Status:** ready-for-agent

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

- [ ] A message reporting an API problem produces a task typed `api_issue`, carrying whatever of environment / correlationId / curl the message contained
- [ ] A request for project permission produces an `access_request` task with the project and permission
- [ ] A question about documentation produces a `doc_question` task
- [ ] Social talk produces no task and is recorded as skipped
- [ ] Salary and off-topic messages are filtered to skip **before** the model is called, not by the model's judgement
- [ ] Triage performs no writes: it reports a decision, and the caller applies it
- [ ] Triage reads the conversation's stored context, so a follow-up referring to an earlier message is understood
- [ ] A follow-up in a conversation that already has an open task updates that task rather than opening a second one
- [ ] A follow-up whose type differs from the open task's is escalated for human input
- [ ] Confidence below the configured threshold is escalated for human input
- [ ] A model error, timeout, refusal, or turn-limit breach is escalated for human input — never discarded
- [ ] Every triage decision is recorded with its type, confidence and parameters, so the threshold can be derived from real data
- [ ] The provider's `base_url`, `api_key` and `model` are configuration, so a different OpenAI-compatible provider can be used without code changes
- [ ] Tracing is disabled, so no traffic or credential reaches OpenAI when another provider is in use
- [ ] Triage runs off a queue: a slow or failing model call never stalls ingestion
- [ ] The operator's own messages are retained as conversation context while still being skipped as triggers

## Notes

Tested with the SDK's `ScriptedModel`, so no test makes a network call.

Structured output via `response_format: json_schema` is rejected by some
OpenAI-compatible providers. Expressing the union as tool calls avoids that, but
the chosen provider should be verified early — it decides whether this ticket's
approach works at all.
