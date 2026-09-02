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

## Transform

Turning a platform message into something worth reading: prose cleaned, code
left exactly as it was, attachments named.

**Split before cleaning** is the whole of it. Stripping an emoji or collapsing
whitespace inside a `curl` or a stack trace corrupts the one part of the
message that has to survive verbatim — and it is the part a value is lifted
out of. So code comes out first, is never touched, and goes back where it was.

Distinct from the **prefilter**, which is not cleaning. Some messages must not
reach a third-party API at all — pay, a medical record, a password, someone
asking for an API key. The harm is in the sending, so it is decided before the
call by a word list the operator maintains, not by a judgement a persuasive
message could argue with.

It **holds**; it does not skip. Several of those words turn up in ordinary
reports, so a rule that dropped them would be losing real mentions on the
strength of one word. The guarantee is that the model does not see it, not that
nobody does.

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

Deciding what a message is. **That, and nothing else.** Produces a **decision**
— a type and a confidence — and writes nothing. Every message gets exactly one
of two outcomes: `Decided` or `NeedsHuman`. There is no third case, and no
silent discard.

Not parameters, not a summary. Classifying and lifting values out of a message
are different jobs with different failure modes: a wrong label sends a task
down the wrong path where somebody notices, a wrong `correlation_id` sends
someone looking through the wrong request where nobody does. Doing both here
put two producers on one set of fields and needed a merge to reconcile them —
and a merge between two models that disagree is a coin toss with a rationale.

The tool schema is what holds the line, because a tool parameter is an
instruction: a schema with `correlation_id` in it *is* triage extracting,
whatever the prompt says.

## Extraction

Lifting the values a task needs out of what the reporter wrote. One extractor
per task type, each owning its prompt, its schema and its model — it knows what
that workflow needs.

It reads **every** message linked to the task, oldest first, not the opening
one. The answer to a question we asked comes back as an ordinary follow-up, and
nothing else in the system reads it for content.

It runs inside `prepare`, node 0 of every graph (ticket 03), because
validation has nothing to check until the fields are filled and runs
immediately after regardless. The first answer for a field stands: a later
run may fill what is still blank and may not revise what it already said,
because a model asked the same question twice does not give the same answer,
and a reworded value is indistinguishable from a changed one.

An extractor may also call **`ask_clarification`** (ticket 05): it just read
the whole thread and may catch something no structural rule does. It names
which of its own fields, closed to that type's own dataclass fields, and
why — intent, never words, so the tool cannot be argued into phrasing that
bypasses the Responder's voice. Code stays the floor: a value the type's own
rules reject is challenged with the code template regardless of what was
asked instead, and a field the model names that turns out already filled is
not asked about again.

## Workflow

What to do about a task. Returns an **action** — `Ask`, `Reply` or `HandOver` —
never a side effect.

Every task takes the same first two steps, whatever its type: **fill in** what
the original message carries that triage did not extract, then **check** the
result against the type's rules. Both happen before a route is chosen, because
they are about the parameters and not about what to do with them. What was
filled in is written back to the task, so the route reads what was checked.

After that, every task type gets its action from a **graph** (ticket 04) — the
**edge router** looks one up for every type `PARAMS` knows about, never
answering "no graph". A type with no investigation of its own gets one node:
validate the parameters, ask for whatever is missing, hand over otherwise.
Most types need nothing more than that; `api_issue` is the one with more.

There is no second way. A registry of per-type planner functions lived here
until ticket 33 emptied it, a deterministic path outside any graph lived here
until ticket 04 removed the branch that read it, and a dispatcher with nothing
to dispatch to is not extensibility — it is a second way to do what the graphs
already do.

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

The override is what decides whether the investigation past node 0 is worth
starting. Without it in `_RULES` — where it was documented but absent — a
report with nothing to trace on validated cleanly, and the graph ran its whole
path to find out it could do nothing. **A precondition belongs in the gate,
not in the last node's else branch.**

## Graph

How a workflow that is more than one decision gets made. A **node** is
`async (state, deps) -> result`; an **edge** may carry a predicate, and the
first whose predicate holds is the one taken. Deterministic Python: the shape
is code, not something a model chooses at run time.

Two things follow from that shape and neither is incidental.

A graph **checkpoints after every node but its entry** — `prepare`, node 0
(ticket 03), which extracts and validates and runs fresh on every pass because
there may be a new message since the last one, and is never itself part of
the checkpoint. Its *output* is what state is discarded against: results are
only meaningful for the inputs that produced them, so a restart resumes past
node 0 exactly when nothing it found has changed, and re-investigates when it
has — otherwise asking the reporter a question and receiving an answer would
change nothing.

A node that cannot decide returns an `Ask` or `HandOver` and the run ends there,
the same as any node deciding the graph's answer — an absent edge past it, not
a special case. `PauseForHuman`, raised rather than returned, used to be a
second way to do this; it dissolved (ticket 04) once new reporter text
re-running from node 1 reached everywhere "resume from the paused node" did.

The composing node's agent reports its conclusion by calling a tool —
`answer(text)` or `hand_over(reason)` (ticket 06) — rather than by writing
prose a node function then parses; `hand_over` alone is any node's, `fix_bug`
included, to call when it cannot conclude. `CANNOT FIX`, `NOT FOUND`, a stray
Markdown fence: a sentinel is a private protocol between a prompt and the
function reading it, and a model that wanders off it fails silently, its
prose read as the answer it never meant to give — `fix_bug`'s own code never
checked for `CANNOT FIX` at all, so a refusal in prose was proposed as the
diff. A graph that reaches its end without a node having answered hands over
by code — "it did not say" is not a question to ask a model.

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

`friday/agent/harness.py` is the only module that may import the agent SDK. The SDK
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

## Outbound state

Where an outbound row is in its life: `queued`, `sent`, `failed`,
`sent_manually`. One definition, in `friday/domain/states.py` beside
`TaskState`, because it was two: the outbox held the set for its readers and
the store held it for its `WHERE` clauses. Two of the outbox's four had no
reader left by the time anyone looked — which is what a duplicated vocabulary
looks like as it rots, one copy quietly going unused.

`sent_manually` is apart from `failed` on purpose: the audit trail should say
"a person sent this" rather than "this was abandoned".

## Outbox

The only module that delivers. Holds outbound intents, dispatches each to the
adapter its `sender` names, retries within a bound, and surfaces what it could
not send. Nothing else calls a provider's `send()`.

## Handled by the operator

A task the operator answered themselves. Not `done` — a person did the work
rather than the agent — and it is reopenable, because closing on "they said
something in this channel" will sometimes be wrong. Everything queued about the
task is withdrawn the moment it is noticed, silently: a message announcing a
cancellation is noise about a thing that correctly did not happen.

Which task their message closes follows the reply rule. A reply names what it
answers, and that task closes; a message replying to nothing closes the
conversation's task only when there is exactly one.

The share of tasks that end here is the one number that says whether this
system is helping.

## Approval

A fact about a **task**, not about a message: who approved and when. An
outbound intent whose kind requires approval is only sendable while its task
carries one.

This is one of two gates, not the only one (ticket 07). A message reaching a
person waits here, at the outbox, whatever kind of task produced it. An
*action* — so far, only applying a fix — waits on the SDK's own tool
approval instead: the tool is marked `needs_approval`, the run stops holding
its state rather than after it, and the state is what `dag_state.interruption`
holds while the operator has not yet said yes or no. Approving resumes the
exact call — in this process or a later one, a restart or a redeploy between
them — rather than re-running the investigation to reach it again; declining
ends the run as a hand-over, no further turn spent asking the model to react
to its own refusal. Reading logs and locating code ask for neither gate —
the risk this exists for is in acting, not in investigating.

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
