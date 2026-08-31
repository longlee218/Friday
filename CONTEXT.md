# CONTEXT

The domain vocabulary for friday-agents. Use these words in code, tests, tickets
and commit messages. Where a word had two meanings, this file picks one.

## Message

An inbound message from a chat platform, normalised to one shape regardless of
where it came from. **A message is not a task.** Most messages are context;
some address the operator, and only a few become work.

A message that addresses the operator carries a **mention type** — `direct`,
`role` or `dm`. A message with no mention type was seen but not addressed to us;
it is kept because a conversation missing half of itself does not read.

One table holds both. The mention type is what separates the triage queue from
the surrounding context, not a second table.

## Conversation

Where an exchange is happening: a channel, a thread, or a DM. Identity is
`(provider, channel_id, thread_id)` — a thread and its parent channel are
different conversations, because they carry different context.

**Not "session".** That word is taken twice over — by the Agents SDK, whose
`Session` is a transcript of an agent's own turns, and by Discord's gateway
session. Say **conversation** for the place, and reserve **agent session** for
the SDK's.

## Task

A piece of work derived from a message: a type, a confidence, and the
parameters extracted from the message. A conversation has at most one open task;
later messages in it are follow-ups, not new tasks.

**Parameters matter more than the type.** The most common real action is
noticing an `api_issue` arrived without an environment or correlationId.

## Triage

Deciding what a message is. Produces a **decision** — a type, a confidence and
parameters — and writes nothing. Every message gets exactly one of two outcomes:
`Decided` or `NeedsHuman`. There is no third case, and no silent discard.

## Workflow

What to do about a task. Returns an **action** — `Ask`, `Reply` or `Park` —
never a side effect.

A planner is a function of the task's parameters. The deterministic ones are
pure and stay that way; one that has to look something up is handed a
**harness** and may be a coroutine. That is where a workflow becomes agentic,
per task type and on evidence, rather than everywhere at once.

`Ask` is the agent's own decision. `Reply` waits for approval — asking for a
correlationId costs a question if it is wrong, and asserting a cause costs the
operator's credibility with their own team.

One rule holds for every type: **a task missing something it cannot work
without has to say so.** Required-ness is read off the parameter type —
`project: str` is required, `doc_ref: str | None` says outright that we can
manage without it — so it is never declared twice and cannot drift from the
schema the model is asked to fill.

`api_issue` overrides that rule, because its own is not expressible as a type:
a correlationId *or* a curl makes a request findable, and both are optional
individually. A type with an override keeps it; everything else gets the
general rule for free.

## Tool server

Tools that live outside this process, reached over MCP. A server is
**configuration** — adding one is a block in `config.yaml`, not a module — for
the same reason `base_url` and `model` are.

Which tools an agent may see is declared beside the server, not left to the
agent's instructions: a prompt is a request and a filter is not. A log server
offers whatever it offers, and nothing about answering "why did this request
fail" should be able to delete a log stream.

## Harness

The one place an agent is *run*. Takes a declared agent and an input, returns an
outcome. Owns everything every agent needs and none of them should restate: the
client and its `base_url` / `api_key` / `model`, model settings, the logging
hooks, turn and token caps, guardrails, handoffs, and the rule that any failure
becomes work for a human rather than silence.

An **agent declaration** is then only what makes that agent different:
instructions, tools, output shape. Triage is one; the responder will be the
second.

Not built yet, deliberately. One agent is a hypothetical seam; two is a real
one. Building it against triage alone would mean guessing at what varies.

## Outbound intent

Something to send, held as data rather than performed as a call. Carries the
conversation, the text, the **sender**, what it replies to, and its **kind**.

The **kind** decides whether it needs approval:

| kind | sender | approval |
| --- | --- | --- |
| `ask_for_details` | user account | no — completing the task's own required parameters |
| `approval_card` | bot | no — it *is* the request for approval |
| `reply` | user account | **yes** — the agent speaking as the operator |

## Outbox

The only module that delivers. Holds outbound intents, dispatches each to the
adapter its `sender` names, retries within a bound, and surfaces what it could
not send. Nothing else calls a provider's `send()`.

## Approval

A fact about a **task**, not about a message: who approved and when. An
outbound intent whose kind requires approval is only sendable while its task
carries one.

## Sender

Which identity speaks: `discord_user` or `discord_bot`. Two Discord identities
run in one process. Everything the outside world sees comes from the user
account; the bot exists to carry buttons, which a user account cannot send.

## Provider

A chat platform as the rest of the system sees it: normalised messages in,
outbound intents out. Platform mechanics stay inside the implementation.

## Sweep

The recovery path. The live connection can miss messages, so channel history is
re-read from a stored **cursor** (`last_seen_message_id`) on a timer and
immediately on reconnect.
