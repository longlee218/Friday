# CONTEXT

Two things, kept apart: the project's current **state**, and the **domain
vocabulary**. Use the vocabulary's words in code, tests, tickets and commit
messages; where a word had two meanings, this file picks one.

# Project state

What is running and what is open, as of 2026-09-18. This part goes stale
fastest — check each ticket's own `**Status:**` line before trusting it.
Architecture is in `docs/DESIGN.md`; how to work is in `CLAUDE.md`.

**Running.** Friday ingests Discord mentions, classifies them, opens tasks,
asks for missing details and sends approved replies as the watched account.
It runs on the operator's own machine, on branch `main`, with a passing
suite.

**Triage, as of 2026-09-20.** Scored 100% on `evals/triage.jsonl` and on a
second, deliberately balanced draft set, three runs each, after the label
definitions were rewritten from the operator's own account of the work, the
thinking flow gained the arrival prior (what reaches triage was addressed to
us, so work is the normal case), the sensitive-word prefilter stopped
treating `luồng` as `lương`, and ten worked examples went into
`config.yaml`. It read 81.0% with 11.4 points of run-to-run spread before
that. The balance set is a draft in
`.scratch/read-it-the-way-the-operator-does/research/04-balance-set-draft.jsonl`
and its labels are the operator's to confirm before it moves into `evals/`.

**Boards** under `.scratch/<feature-slug>/issues/`:

- `discord-mention-triage/` (from `docs/SPEC.md`) — the original board. Done.
- `a-monitor-on-the-whole-path/` — the operator UI rebuilt as a real-time
  monitor: SSE live feed, drill-down to a Flow screen, motion, skeleton and
  toast, axe-core and Lighthouse gates; a React + Vite SPA inside this
  repository. Done.
- `every-task-is-a-graph/` — one engine: `friday/tasks/` (the pool) and
  `friday/dag/` (the graph). Every task type is a graph of one node. Done.
- `every-answer-has-a-shape/` — structured answers and the agent memory
  tools. Done except ticket 03, `ready-for-human`: the eval rows are the
  operator's to label, since a classifier scored against labels a model
  chose measures nothing.
- `work-that-has-gone-cold/` — `max_message_age` and the cold-cursor sweep.
  Done.
- `read-it-the-way-the-operator-does/` — the `api_issue` graph, designed from
  the operator's own routine. Done: 09 (twelve memory kinds), 10 (YAML
  context files gone), 11 (one invoke in the runner), 12 (approval per
  outbox row), 13 (bounded pool concurrency), 16 (measurements and the model
  probe, except the reporter-delay measurement), and **00's code** — the
  six-node slice `Prepare → Resolve → FindRequestLog → ReadFailingCode →
  Diagnose → Report`, in `friday/dag/api_issue/`. `api_issue` is no longer a
  one-node graph: a complete report is investigated rather than handed back.
  **00 is not finished**: its five runs on five past cases have not happened,
  because the cases are the operator's to pick, and the Loki branch has never
  been called against the real server.
  Two defects the live task 6 exposed are their own tickets: 17 (the
  reporter's Bearer token is stored and re-sent verbatim) and 18 (the curl is
  retyped by the model, and task 6's copy lost a character). One open
  question, the operator's: may Friday replay a request when no log line can
  be found? Waiting on the operator: 07 (confirm the drafted knowledge rows
  in `research/03-seed-rows.md`, write the runbooks), the two `route` rows and
  one `service` row the slice reads — `memories` is empty, so every run hands
  over on a missing row until they exist — cases 2–5 and case 1's cause. The
  order is in its `execution-plan.md`.

# Vocabulary

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

The type is a member of one **closed decision set** — every task type, plus
`skip` for a message that needs no action — checked in this process before
anything acts on it. Two tools carried this, one naming work and one naming
the absence of it, until board `every-answer-has-a-shape` found that the split
meant two validations of one question: an invented type could open a task the
pool then discovered had no graph, and "there is no work here" was checked less
strictly than "there is".

So a `NeedsHuman` now says *which* failure it was. A model that named something
outside the set is not a provider that never answered: one says a prompt or a
model is wrong, the other says the network was, and the classifier's own
evaluation reports them as two numbers.

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

An extraction is **one validated object**: that type's own parameters plus
**`ask_about`** and **`because`** — which of its own fields the extractor wants
the reporter asked about, and why. It just read the whole thread and may catch
something no structural rule does. The field names are closed to that type's
own dataclass fields, and it names fields rather than words, so it cannot be
argued into phrasing that bypasses the Responder's voice. (Two tools carried
this before board `every-answer-has-a-shape`: `ask_clarification`, which
nothing ever called, and `ask_for_fields`, whose enum is what `ask_about`
inherited.) Code stays the floor: a value the type's own rules reject is
challenged with the code template regardless of what was
asked instead, and a field the model names that turns out already filled is
not asked about again.

## Extraction mark

What node 0's last **Extraction** for a task was made from, and what it came
to. One row per task, rewritten whenever the reporter says something new.

Node 0 re-executes on every pass, and that is deliberate — it is excluded from
the checkpoint because a reporter who sends the curl three seconds later has to
be read. What it must not do is call a model when nothing arrived. One task in
the recorded data has two extractor calls of 1,790 input tokens whose prompts
share a sha256, seven and a half hours apart: it sat pending across a restart,
and every pass paid again.

The fingerprint is over **the reporter's text and the field schema, and nothing
else**, because that is the whole of what the extractor is shown. The task's
parameters never reach its prompt, so a parameter that moved is not a reason to
pay for the same answer again. The schema is in there rather than assumed
fixed: adding a field, or rewording what one means, changes the prompt, and a
task already marked would otherwise never be read again under the new one.

A mark stands in for the call, so it records the outcome and not merely the
input: the extracted values, so the same fill happens, and the question the
extractor asked, so the same question is asked. Without the question a skip
would turn an `Ask` into "everything needed is here" on the next pass — the
fields an extractor asks about are usually the optional ones no structural rule
challenges.

Distinct from the **Graph**'s own checkpoint, which is also one row per task. A
checkpoint holds what nodes *returned* and is discarded when the task's
parameters change; a mark holds what node 0 was *given*, and outlives a
parameter change on purpose.

## Pool

The loop. Pulls pending tasks and hosts their graphs — nothing about *what*
to do with a task is this module's decision, only *when* and *whether what
came back may be sent*. Four things, every pass: stand down for a task the
operator answered themselves, announce to the operator whatever nobody can
act on, host the graph for whatever tasks are pending, and turn what came
back into rows — a reply queued for approval, a question sent outright, a
task moved to `needs_human`.

Lives in its own `friday/tasks/` package, not inside the graph engine:
hosting a graph is one of the four things it does, not what it is. It shares
no vocabulary of its own — `Ask`, `Reply` and `HandOver` come from the
domain, same as the graph engine reads them, which is what lets the two stop
importing each other. Was a differently-named loop package once, before
every task type ran through the same graph engine and there was no second
thing left in it to share a package with.

## Deciding an action

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

**Nothing produces a `Reply` today.** The tool that built one belonged to the
five-node `api_issue` graph, and that graph is gone — so the system can ask a
reporter or hand over to the operator, and cannot answer. The vocabulary and
the outbox path for a reply are intact and unused, waiting for a graph that
concludes something.

**All three are read by the reporter except one, and it is not the one that
waits.** `Ask` and `Reply` both go to the person who reported the problem;
`Reply` waits precisely *because* it answers them in the operator's name.
`HandOver` is the one addressed to the operator, and it never reaches the
reporter at all. The operator's other two messages are not actions: the
approval card queued beside a `Reply`, and the help-wanted sent about a task
sitting in `needs_human`.

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

The checkpoint is keyed on the graph's **version** as well as its name — a
digest of its node names and edges (`DAG.version`), so a renamed, added or
reordered node never inherits a result recorded under the old shape.

Every node runs through **one invoke** (`DAGRunner._invoke`, board
`read-it-the-way-the-operator-does` ticket 11): the node's own
`timeout_seconds`, its retry over an explicit `retry_on` list with doubling
backoff, and any other exception turned into the node's result rather than
raised. A result that is not an `Action` may be an **envelope** — a JSON dict
with `status` (`ok`, `empty`, `skipped`, `timed_out`, `error`) and `reason`;
the runner writes the last two itself, the run goes on along the edges, and a
graph that ends on one hands over naming it. Neither is checkpointed as done.
Each attempt is a **node run**, one `node_runs` row per attempt, node 0
included. A node that calls a model names its `agent`, and registration
refuses its timeout unless it outlasts that agent's by a margin — two equal
clocks race, and the outer one's cancellation is invisible to the harness.

A node that cannot decide returns an `Ask` or `HandOver` and the run ends there,
the same as any node deciding the graph's answer — an absent edge past it, not
a special case. `PauseForHuman`, raised rather than returned, used to be a
second way to do this; it dissolved (ticket 04) once new reporter text
re-running from node 1 reached everywhere "resume from the paused node" did.

The composing node's agent reported its conclusion by calling a tool —
`answer(text)` or `hand_over(reason)` (ticket 06) — rather than by writing
prose a node function then parses. Both went with the five-node `api_issue`
graph; the argument is why an agent with a declared shape answers through a
generated tool (see **Harness**), which is a different `answer` from that one
and worth not confusing with it; `hand_over` alone is any node's, `fix_bug`
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

**The last node is the exception**: when *it* has no agent, the graph hands
over rather than answering (ticket 10). Everything it holds by then —
the analysis's cause, a proposed diff — is Node-family text, and only
Responder-family agents produce what a reporter reads. It replied once,
carrying both verbatim, which put an unreviewed patch in front of the person
who filed the report; the operator is who that material was always for.

## Tool server

Tools that live outside this process, reached over MCP. A server is
**configuration** — adding one is a block in `config.yaml`, not a module — for
the same reason `base_url` and `model` are.

Which tools an agent may see is declared beside the server, not left to the
agent's instructions: a prompt is a request and a filter is not. A log server
offers whatever it offers, and nothing about answering "why did this request
fail" should be able to delete a log stream.

## Voice

How an agent is told to write, as distinct from what it is told to do. It is
part of that agent's own prompt, in that agent's own module — there is no
separate file and no label deciding who gets which section.

Two agents carry the operator's voice, because a person reads what they write
under that name: the **responder**, and the graph node that composes a reply.
Every other graph node is told the opposite — it is a step, it writes to the
next step, it invents nothing. Triage and the extractors are told nothing
about voice at all: one picks a tool, the other copies values, and a word
spent on voice there is paid for on the highest-volume calls in the system to
change nothing.

The two who share the voice hold two copies of it, deliberately. They are
different agents with different jobs, and the day one needs a sentence the
other does not is the day sharing it would have been the bug.

There was a `PERSONA.md` holding this for everyone, split by heading, with a
`Family` label deciding which agent read which section. It went with ticket
16 — knowing what an agent had actually been told required opening a second
file, and the invariant the label was there to protect turned out not to be
protected by it: the test written in terms of the label passed throughout the
bug it existed to catch. What a reporter reads is pinned on the one place a
`Reply` is built instead.

Distinct from **tone**: the voice is written by hand and describes the shape,
the tone examples are real messages the operator sent and are the evidence.
Where they disagree the examples win, and the voice says so itself.

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

## Flow

Everything that followed from one message: the turn it belonged to, what triage
concluded and how confident it was, the task if one opened, every model call and
tool call in order, and the outbound rows at the end. A read-side assembly, not
a thing that is stored — `Database.flow_for` builds it at one instant and
`MessageFlow` is its shape.

**Its spine is a message, not a task or a graph**, and both exclusions are
deliberate. A task-spined flow loses triage, because when the classifier runs
there is no task and its call correlates by message alone; it also loses every
`skip`, which is the outcome an operator most often wants to interrogate. A
graph-spined flow would draw one box: every task type gets the same one-node
graph, so the multi-step thing here is the path through the process, not the
`Workflow`.

Not to be confused with `Workflow`/`Graph`, which is what a task's own DAG does
once it is running. A Flow contains one of those as a step.

## Channel summary

What a room's transcript has been reduced to: four fields — `topic`, `facts`,
`decisions`, `constraints` — written by the summariser and read by every later
agent that reads the room's context, not a paragraph of prose. Structured
because free prose loses too much: "the reporter had a problem and we
discussed it" is not context a later run can act on.

Written when the room has said anything since its last summary, with no other
gate: the fraction-of-a-context-window threshold this used to wait for made
the summariser an emergency valve rather than a context-building step, and
every room's derived context stayed `{}` because of it, alongside the
mechanism being unconfigured. One message is enough to be worth a call now.

**Capped, and a cap refuses rather than trims** — the same rule as
`daily_token_budget`. A summary cut mid-field says something false about the
room; the summary already stored is merely older, and stands when a fresh one
is refused. Measured on the stored, structured form, not the transcript that
produced it.

**A row, one active per room** — a `summary` **Memory** (board
`read-it-the-way-the-operator-does`, ticket 10). It was the `derived` section
of a per-channel YAML file, replaced wholesale on each rebuild; a rebuild now
supersedes the previous row, so what a room used to be summarised as is kept.
Read by triage and the responder, and by no other agent.

Bookkeeping — the first and last message it covers, and the shape it was
written to — is on the same row, in `data` beside the four fields, and is
never rendered: the renderer reads the four fields by name and nothing else,
because a message id is not context. (In the file it was a separate `state`
section, for the same reason.) Distinct from an **Extraction mark**'s checkpoint in the same way that
one is: this is what a room *is*, not what a task's own extraction was made
from, and it outlives a task closing.

A model that answers in prose rather than the four fields asked for still said
something true about the room; that answer is kept as `topic` rather than
discarded, because the alternative to an imperfect fact is no fact at all.

## Friday state

What one message's journey knows about itself, carried the whole way down. The
room and the agent now running, and — as the journey supplies them — the
provider, thread, message, author, reply and task. It is what the SDK's per-run
`context` carries, and the only thing it carries: that slot used to mean "who
is this run about" for one agent and "where the answer will appear" for
another, which is two mechanisms sharing one parameter.

Its fields are read-only, and every change goes through a named method that
returns a *new* state — `as_agent`, `for_task`, `about_message`. Not a style
choice: one value reaches a tool, the store and the recording sink inside a
single run, and a field anything could assign makes "what can change this, and
where" unanswerable, which is the question it exists to keep answerable. There
is deliberately no general setter.

Only the room and the agent are required. Those two are the boundary and the
provenance — a memory written without a room has nowhere safe to live, one
written without an author loses who wrote it and while doing what. The rest are
absent until the journey supplies them, the way a task id already meant "this
run belongs to no task".

Three readers so far: the memory tools take it as their scope, the recording
sink reads the message and the task off it, and the responder stamps its own
name on it before writing anything down.

What made the slot mean two things was that a tool could write its result into
an object the caller read back afterwards. An answer is the return value of the
call that asked for it now, so there is nothing else for the slot to carry.

## Memory

Something an agent chose to write down, scoped to one channel, reached through
`memory_search`, `memory_add`, `memory_update` and `memory_delete`.

This replaced an **observation → note** pipeline: a step staged a guess, and a
promotion pass turned it into something believed only once an approved outcome
corroborated it. The staging tier had no drift floor problem — an agent never
read its own unreviewed guesses back — but it had no producer either, for as
long as the fact mattered: nothing wrote an observation once `remember` was
removed from the tool list, so the tier promoted nothing for months before it
was finally dropped.

A memory trades that floor for three narrower guarantees instead. It reaches a
model **only as a tool result**, never appended to an agent's instructions —
closed by construction, not by escaping, since a tool result cannot rewrite the
prompt of every later call the way `instructions` can. **Scope is
runtime-supplied**, carried on **`FridayState`** and read off the run's context
rather than named by the model: a channel's memory is invisible to a run in
another one. (It was its own `MemoryScope` until board
`every-answer-has-a-shape`, which is the same four facts under a second name —
deleted rather than aliased, because a second name for one thing is how two
things drift.) **Ids are opaque and sparse**, so a model that invents one fails
rather than landing on a neighbouring row.

Drift is possible now and is bounded differently: by the channel scope, by the
operator's visibility into what was written and by whom, and by the fact that
nothing reaches a prompt except through a tool call the run chose to make.

A line shaped like a directive at this system's own mechanism — "send without
approval", "skip the validation" — is refused before it is written, at the one
write path every producer shares, because a memory is read back as fact by a
run with none of the context that produced it.

**Twelve kinds, one table, and no files** (board
`read-it-the-way-the-operator-does`, tickets 09 and 10). A memory row has a **kind** — `fact`, `constraint`, `decision`,
`finding`, `voice`, `runbook`, `summary`, `project`, `service`, `route`,
`dependency`, `person` — and who reads it follows from the kind
(`readers_for`). A model sees and writes only the first five
(`ModelMemoryKind`). A row also has an **origin** — `model` or `admin`, the
operator typing it on the board — and a model may not update, supersede or
delete an admin row. A structured kind carries its payload in `data`,
checked against that kind's schema when written, and a natural **key**
(a service's name, a route's domain) that one active row per room may hold.
The exception is `finding`: its key, `service:error_code`, names the fault
rather than the finding, and findings on one fault pile up — several saying
the same thing are the signal a runbook is owed.

Every memory is a row: there is no second store. What the operator had written
in a channel's YAML context file — its `overrides` — is `fact`, `constraint`
and `person` rows with origin `admin`; what was `base.yaml`, true of every
room, is rows whose channel is `*`; the summariser's `derived` is the room's
`summary` row. The extractor is shown the domain kinds for its room and for
`*`, labelled by who is answerable for each line.

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

## Hand-over

An `Action` a node's agent produces, by calling `hand_over(reason)`, when it
cannot conclude — the fix is not obvious, the evidence is not enough to write
a reply, the type is one nothing here knows how to work with. `reason` is the
agent's own finding, quoted to the operator directly; it is not a message
under the operator's name to anyone else, so it never waits at the outbox.
The task moves to `needs_human` and the pool announces it, once, with
whatever the paused node actually said.

**Not "handled by the operator"**, the state this ends in only after a
person acts on it. A hand-over is the system saying it does not know what to
do; the operator answering is a different fact entirely, recorded below.

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

A fact about one **outbox row**, not about its task: who approved that reply
and when. An outbound intent whose kind requires approval is only sendable
while the row itself carries one, and the approval card names the row it asks
about (`approves`), so answering it releases that reply and nothing queued
after it.

It was a fact about the task until board `read-it-the-way-the-operator-does`
ticket 12 (finding A): written once and never cleared, so approving a task's
first reply approved every reply it queued later — the one that asserts a
cause included — and those went out unread. The migration carried each
approved task's approval onto the replies it already had.

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

**An approval expires with the work it belonged to** (ticket 12). Approving
runs the tool, and there is no undoing that and finding out afterwards, so
three things are checked before anything resumes: the task is still waiting
on a person, its parameters are still the ones the paused run was worked out
against, and the call is still there to approve. The operator answering in
the channel withdraws it outright, alongside the queued messages — the same
sentence covers both, because a patch left behind is a side effect on work
somebody already finished.

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

A channel with no cursor — a fresh database, or a redeploy that lost the
volume — is a **cold cursor**, and it is read from the other end: back as far
as the **lookback** and no further, newest end first, delivered oldest first.
The lookback is `max_message_age`, the same number that decides a turn is
`outdated`, because both answer "work this old is not worth starting".
