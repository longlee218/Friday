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

Every task takes the same first two steps, whatever its type: **fill in** what
the original message carries that triage did not extract, then **check** the
result against the type's rules. Both happen before a route is chosen, because
they are about the parameters and not about what to do with them. What was
filled in is written back to the task, so the route reads what was checked.

After that, there are two ways a task type gets its action, and which one it
uses is decided by the **edge router**: a task type with a **graph** goes to the graph,
and one without takes the deterministic path — validate the parameters, ask for
whatever is missing, park otherwise. Most types need nothing more than that.

There is no third way. A registry of per-type planner functions lived here
until ticket 33 emptied it, and a dispatcher with nothing to dispatch to is not
extensibility — it is a second way to do what the graphs already do.

`Ask` is the agent's own decision. `Reply` waits for approval — asking for a
correlationId costs a question if it is wrong, and asserting a cause costs the
operator's credibility with their own team.

One rule holds for every type: **a task missing something it cannot work
without has to say so.** Required-ness is read off the parameter type —
`project: str` is required, `doc_ref: str | None` says outright that we can
manage without it — so it is never declared twice and cannot drift from the
schema the model is asked to fill.

`api_issue` overrides that rule with `OneOf`, because its own is not
expressible as a type: a correlationId *or* a curl makes a request findable,
and both are optional individually. A type with an override keeps it;
everything else gets the general rule for free.

The override is what decides whether a graph runs at all. Without it in
`_RULES` — where it was documented but absent — a report with nothing to trace
on validated cleanly, and the graph ran its whole path to find out it could do
nothing. **A precondition belongs in the gate, not in the last node's else
branch.**

## Graph

How a workflow that is more than one decision gets made. A **node** is
`async (state, deps) -> result`; an **edge** may carry a predicate, and the
first whose predicate holds is the one taken. Deterministic Python: the shape
is code, not something a model chooses at run time.

Two things follow from that shape and neither is incidental.

A graph **checkpoints after every node**, so a restart resumes rather than
re-running work that cost money. Results are only meaningful for the inputs
that produced them, so state is discarded when the task's parameters change —
otherwise asking the reporter a question and receiving an answer would change
nothing.

A node that cannot decide **pauses** rather than guessing: `PauseForHuman`
carries the question, which becomes a `Park` and reaches the operator with the
question intact. It is not a failure and not an outcome — it is the graph
stopping to ask.

An agent is a node inside a graph, never the thing driving it. Which agent a
node gets, and which tool server, is composition — handed down through `deps`,
so the shape of a graph can be tested without a model or a server. A node whose
agent or server is absent skips and returns nothing; the graph still reaches
its last node, which is the only one that decides what to send.

## Tool server

Tools that live outside this process, reached over MCP. A server is
**configuration** — adding one is a block in `config.yaml`, not a module — for
the same reason `base_url` and `model` are.

Which tools an agent may see is declared beside the server, not left to the
agent's instructions: a prompt is a request and a filter is not. A log server
offers whatever it offers, and nothing about answering "why did this request
fail" should be able to delete a log stream.

## Persona

Who an agent is, as distinct from what it does. One file for the whole system,
because "you are Long Lee's assistant, and people read Vietnamese" is not a
fact any single agent owns.

Three **modes**, chosen per agent: the full thing, the identity and language
rule without the voice, or nothing. The middle one exists because an agent
filling in a field something else validates must not also be told to write in
Vietnamese — `environment` has to be `production`, not `sản xuất`.

Distinct from **tone**: the persona is written by hand and describes the shape,
the tone examples are real messages the operator sent and are the evidence.
Where they disagree the examples win, and the persona says so itself.

## Harness

The one place an agent is *run*. Takes a declared agent and an input, returns an
outcome. Owns everything every agent needs and none of them should restate: the
client and its `base_url` / `api_key` / `model`, model settings, the logging
hooks, turn and token caps, guardrails, handoffs, and the rule that any failure
becomes work for a human rather than silence.

An **agent declaration** is then only what makes that agent different:
instructions, tools, output shape. Triage was the first; the responder, the extractors and every
reasoning node in a graph followed.

`friday/harness.py` is the only module that may import the agent SDK. The SDK
is here for speed, not for keeps, and that is only true while replacing it
means rewriting one file.

## Observation

Something a step learned while working — a fact, a person's habit, a lesson.
Written to a **staging tier** and read by nothing.

That restraint is the point. An agent given its own unreviewed notes as context
drifts, and the drift has no floor; it is the same failure as learning a voice
from its own replies, one layer up. Promotion is a separate pass, over work a
human approved.

The runtime attaches the task and the time rather than asking for them: a model
asked for a timestamp invents one, and a model asked which task it is working on
is sometimes wrong.

## Note

Something believed for longer than one task. An observation becomes one only
when a human approved the work it came from, and — for the categories that need
it — when more than one approved task said the same thing.

`fact` and `person` need two: a reading of how something works can be wrong, and
a habit seen once is not a habit. A `lesson` needs one, because the operator
already made that judgement by approving the work.

Rendered as a block rebuilt from the believed set in a stable order, and reached
through an agent's **instructions**. That is the early part of a prompt, where a
byte that moves costs a cache hit on everything after it.

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
