# Spec: what the room already knows

Status: ready-for-agent. The design was reached in one brainstorming session on
2026-09-08, from reading the recorded prompts of a single live mention
(`discord:1546809275299663952`) rather than from reading code. Every decision
below is closed; the numbered decisions D1–D24 are the rationale, and the
tickets reference them rather than repeating them.

## Problem Statement

I followed one mention through the whole system and read every prompt it
produced. Four things were wrong, and all four were about context.

Triage — whose entire output is a type and a number — was handed 2,783
characters of input: twenty-four lines of transcript spanning seven days. Every
line carried the same author name, because this room is a self-test channel and
the renderer emits `[time] author: text` with nothing to say whether a line was
a reporter asking or the account answering. The model invented a colleague
called "Nhím". That name appears nowhere as a speaker; it appears inside the
body of one message. Its system prompt was 2,272 characters, of which 1,008
were a catalogue of skills it has never once fetched. The last message appeared
twice, because the turn is appended to a context window that already contains
it and nothing deduplicates on message id.

The extractor spent three model calls and 10,249 input tokens on a single
one-line message, two of those calls fetching skills about how to write replies
— which it does not write. It could not decide whether `test.apero` is staging
or dev, so it asked the reporter. The reporter has not answered, and nothing in
the system knows it is waiting.

Then I went looking for the layer that was supposed to prevent this, and found
five, all empty. The `memories` table has zero rows, though the tools that
write it have been wired to the responder since ticket 09. Every channel's
`derived` context is `{}`. The summariser is commented out of `config.yaml`, so
nothing would write it even if a channel asked. The `memory()` prompt section
has had no caller since the day it was written. And a fifth — the promotion
pass over staged observations — ran on every heartbeat over an empty table for
months before it was deleted.

So the problem is not that this system needs a context layer. It is that it has
built five and never produced one line of context, and each failure was silent
under a green suite.

## Solution

Name the four kinds of state separately, and stop asking one mechanism to be
all of them.

Context is what was said recently, and it is built twice, because the two
readers need different things. Before triage, a light build: this person's turn,
plus a short structured summary of the room, under a token budget — because
triage does not need to know what `test.apero` is, only what kind of request
this is. After a task exists, at node 0, a full build: the parameters already
known, the room's domain facts, the artifacts, the messages not yet summarised,
and the questions we have asked and not had answered.

Compaction happens by budget, not by message count, and a model only ever
summarises prose. The values that must survive a machine comparison never reach
it: a field the schema names is compacted into the task's parameters, where the
extractor already copied it verbatim; code, stack traces, logs and SQL are
compacted into artifacts, which are pointed at, never paraphrased. Raw messages
are never deleted — a summary is derived state that names the range it covers.

Memory splits by who reads it. How this room is spoken in is voice memory, it
lives in the channel's context file, and the responder already reads it. What
things in this room actually are — `test.apero` is staging — is domain memory,
it lives in the memories table with a kind, and the extractor and every later
node read it. Neither half needs a new store. Memory gains a kind, a status and
a superseded-by link, so a decision that changed does not sit beside the one it
replaced with nothing to say which is current.

And the board's first law, because five empty mechanisms is a pattern and not an
accident: every store ships with its producer *and* its consumer, in the same
ticket. No ticket builds a table. No ticket builds a seam.

## User Stories

1. As the operator, I want triage to be shown this person's turn and a short summary of the room, not seven days of transcript, so that a classifier deciding one label is not paying for a week of history.
2. As the operator, I want every line of rendered conversation to say whether the account or somebody else wrote it, so that a model cannot mistake our own previous answer for something the reporter told us.
3. As the operator, I want the model never again to name a person who exists only inside the body of a message.
4. As the operator, I want a message to appear once in a prompt however many paths put it there, so that a turn of three messages is not rendered as six.
5. As the operator, I want triage's prompt to contain instructions for classifying and nothing else, so that the highest-volume path in the system stops carrying a catalogue it has never used.
6. As the operator, I want triage's tool schema to stay a type and a confidence, so that widening context does not quietly turn the classifier into an extractor.
7. As the operator, I want the extractor to know what `test.apero` is, so that it fills the environment field instead of asking somebody who already told us where the request went.
8. As a reporter, I want to be asked only for what I have not already said or what the room does not already know.
9. As a reporter, I want the `curl` I pasted to reach the person who will run it exactly as I typed it, with its line breaks, after any amount of summarisation has happened around it.
10. As a reporter, I want a correlationId I sent a week ago to still be matched by machine, not paraphrased into something that no longer resolves.
11. As the operator, I want a stack trace or a log dump to be stored and pointed at rather than summarised, so that no model gets the chance to rewrite it.
12. As the operator, I want the raw messages kept forever, so that a summary written under a worse definition can be rebuilt under a better one.
13. As the operator, I want a summary to say which messages it covers and which version wrote it, so that I can tell what a stale summary was made from.
14. As the operator, I want compaction to trigger on a budget rather than a message count, so that twenty short messages and twenty long ones are not treated the same.
15. As the operator, I want the budget described honestly as an estimate, because there is no tokenizer for the model this system actually calls.
16. As the operator, I want no budget set to mean no compaction, so that the measurement runs from the first day and I set the ceiling once I know what a normal room costs.
17. As the operator, I want the system to know which questions it has asked and not had answered, so that it stops asking again and I can see what it is waiting for.
18. As the operator, I want the list of unanswered questions derived from what was actually sent, with no model involved, so that it cannot be wrong in an interesting way.
19. As the operator, I want to write a fact about a room by hand and have the extractor use it on the next task, so that the first thing I can do about a wrong answer is fix it directly.
20. As the operator, I want a fact I wrote by hand to outrank one a model derived, so that a correction sticks through the next rebuild.
21. As the operator, I want a fact learned in one room to stay invisible in another, because the rooms are different teams and a fact in one is a leak in the next.
22. As the operator, I want a fact that is true everywhere to have one place to live, so that I am not copying the same line into every room.
23. As the operator, I want memory to carry what kind of thing it is — a fact, a constraint, a finding, a decision, or how this room is spoken in — so that the agent that writes replies is not shown environment mappings and the agent that fills fields is not shown tone rules.
24. As the operator, I want the reader of a memory to follow from its kind, so that nothing has to remember a mapping in two places.
25. As the operator, I want a decision that changed to supersede the one it replaces, so that retrieval never returns both without saying which is current.
26. As the operator, I want to see on the board that a memory used to say something else and when it changed, so that a superseded decision is history rather than a deletion.
27. As the operator, I want correcting the wording of a memory to be a different operation from replacing what it claims, so that a typo fix does not look like a reversal.
28. As the operator, I want a channel that has filled its memory to refuse the next write rather than evict something quietly, so that nothing is lost that nobody decided to lose.
29. As the operator, I want a full memory to be visible to me on the board and in the heartbeat, so that the one party who can clean it up is not the one party who cannot see it.
30. As the operator, I want a candidate memory an agent proposes to wait for my mark before anything reads it back, so that an unreviewed guess never becomes a fact the next task acts on.
31. As the operator, I want the gesture that confirms a memory to be the gesture that already confirms a classification, so that there is one thing to learn.
32. As the operator, I want no ticket in this board to ship a store without the thing that writes it and the thing that reads it, because five context mechanisms in this repo are empty and every one of them was empty silently.
33. As the operator, I want the context builder to produce content and each agent family to render it, so that this is not the shared prompt shape that ticket 45 deleted.
34. As the operator, I want a section describing memory to render nothing when there is no memory, so that no agent is told about a door that is not in the room.
35. As the operator, I want a summary stored plain and escaped once where it enters a prompt, so that a hallucinated line is data rather than a section.
36. As the operator, I want a summary unable to forge a second key line in the section that describes the room, because escaping leaves newlines alone.
37. As the operator, I want node 0 to stop paying for an identical extraction on every pass, so that a task waiting for details in a busy room does not re-bill the same prompt forever.
38. As the operator, I want the extractor shown what it has already worked out, so that a second pass reads only what arrived since the first.
39. As the operator, I want no embedding model and no vector store in this work, because the hard cap is two hundred memories per room and a linear scan over that is exact, free and debuggable.
40. As the operator, I want the reversal of ticket 26 written down where the tickets can see it, along with what the unbounded window was buying, so that nobody re-derives the sliding window six weeks from now.
41. As the operator, I want the three tests that pin the unbounded window rewritten at the summary layer rather than deleted, so that prompt-prefix stability is still asserted, one level up.
42. As the operator, I want the classifier's accuracy measured against the frozen evaluation set before and after its prompt changes, because a scripted transport pins wiring and says nothing about whether the classifier is right.
43. As the operator, I want every new guard deleted once and watched go red, because the guard that was only written down is the one that drifted.
44. As a later node in a graph that does not exist yet, I want the context this task assembled to be readable from the run's own state, so that reviving a multi-node graph is adding nodes and not adding a context mechanism.

## Implementation Decisions

**D1 — Four kinds of state, named separately.** Conversation context (minutes
to hours), conversation summary (hours to days), task state (until the task
closes) and long-term memory (weeks to years) are four things with four
lifetimes. They are not one blob. Each has one store and a stated set of
readers. The failure this prevents is the current one, where a single unbounded
message window is asked to be all four and succeeds at none.

**D2 — The board's first law: producer and consumer ship together.** No ticket
may create a store, a column, a section builder or a config key without, in the
same ticket, the thing that writes it and the thing that reads it into a
prompt. Five context mechanisms in this repo are empty; every one of them was
built as a seam awaiting a producer, and every one stayed empty under a green
suite. A ticket that cannot name both halves is not ready.

**D3 — The context builder runs twice, with different content.** A light build
before triage; a full build at node 0. They are different modules with
different budgets and different inputs, because their readers answer different
questions. Triage decides a label and needs the turn plus a summary. Node 0
fills fields and needs the parameters, the room's facts, the artifacts and the
unread messages. Neither is a subset of the other and neither is configurable
into the other.

**D4 — The context package returns content, never rendered prompt text.** The
builders return a `BuiltContext` value: which memories were chosen, which
messages, which artifacts, the summary, the open questions, and what the budget
allowed. Each agent family's own prompt module renders its slice through the
existing section builders. The context package must not import a section
builder, construct a section, or join anything. This is the distinction ticket
45 turned on: it deleted shared *shape* — one dataclass with a fixed section
order for every family — and left shared *content* untouched. Two `ast` tests
already police the render side and will police this: instructions must be built
by the one assembler, and no prompt module may construct a section by hand.

**D5 — The budget is an estimate, and says so.** Characters divided by four.
The configured provider is MiniMax, for which there is no tokenizer; a
tokenizer for a different vendor would be confidently wrong rather than
roughly right. The existing summariser already estimates this way and the
comment there says why. Config names the field so that nobody reads it as
exact.

**D6 — The budget is primary; the message count is a secondary cap.** A count
alone cannot tell twenty short messages from twenty long ones, which is the
whole reason the current arrangement fails. A count is still kept as an upper
bound so a pathological room cannot produce an enormous single build.

**A budget clause that cannot be evaluated fails loudly; it is never
dropped.** This is not defensive coding, it is the shape of the failure this
whole board exists to correct. DeerFlow's compaction takes a list of trigger
clauses and drops any it cannot evaluate — a fraction-of-context clause whose
model exposes no context window — with a warning; and if every clause is
dropped, automatic compaction silently never fires again. That is precisely how
five context mechanisms in this repo came to be empty. An unevaluable clause
raises at startup, where the operator is looking, rather than degrading into an
absence at run time, where nobody is.

**Compaction that achieved nothing does not immediately run again.** Node 0 is
excluded from the checkpoint and re-executes on every pass, by design, so a
compaction that fails to reduce anything would re-run and re-bill on every
pass — the same failure D23 corrects for extraction, one layer up. The trigger
therefore carries a cooldown and an ineffectiveness breaker: after two
compactions that did not reduce the build below the budget, compaction stops
being attempted for that conversation and the condition becomes visible to the
operator rather than a repeating cost. Prior art is Hermes, whose compaction
check returns a reason alongside its verdict for exactly this purpose. Hermes
also ships its per-exchange rolling summarisation disabled by default, because
"every pass rewrites the prompt prefix and breaks the provider prompt cache" —
which is D24's concern arrived at from the other side, and is why this board
does not do rolling summarisation at all.

**D7 — An unset budget means no compaction.** Same doctrine as the daily token
budget: the measurement is on from the first day, the ceiling is something the
operator sets once a normal room's cost is known, and a number guessed before
then would make the first busy day look like a fault.

**D8 — Compaction splits three ways, and a model only ever summarises prose.**
A field the parameter schema names is compacted into the task's parameters,
because the extractor has already copied it verbatim and the store already
persists it. Code, stack traces, SQL, logs and configuration are compacted into
artifacts: stored whole, referenced by id and one line of description, never
shown to a summariser. Everything left is prose, and prose is the only material
in a message with nothing that must survive a machine comparison. The module
that already splits code out of prose exists for exactly this reason and its
docstring names the extractor as the cause.

**The reader of an artifact may not itself produce one.** Otherwise a large
artifact read back into a build is spilled to a second artifact, which is read
back, and so on. DeerFlow hit this and exempts its file-reading tool from its
own output-spilling middleware for that reason.

**D9 — The summary is structured, six fields.** Topic, facts, decisions,
constraints, open questions, artifacts. Free prose loses too much: a summary
reading "the reporter had a problem and we discussed it" is not context. The
`facts` field is the candidate feed for domain memory, which is what connects
this half of the board to the other. `goals` is deliberately absent — this is a
room where things are reported, not planned.

**D10 — `open_questions` is derived, with no model.** It is the outbound
requests for details that have no subsequent reply from the reporter. That is a
query. It is the single most valuable field in the summary and the cheapest,
and it fixes the observed failure directly: the system asked for an environment,
nobody answered, and nothing knew it was waiting.

**D11 — Raw messages are never deleted; a summary is derived state.** It
records the first and last message id it covers and the version that wrote it.
The store's own reasoning for keeping every message already says why: a message
discarded at write time can never be reconsidered under a better definition of
relevance later.

**D12 — A summary is stored plain, capped, and framed so it cannot forge its
own boundary.** Stored plain and escaped once at the section seam: the existing
channel context unescapes before storing for this reason, and the existing
summariser had to be taught it.

**A stored section defends two delimiters, and it takes two defences.** The
outer one is the section's own label: content that can open a line can write a
label, so content is indented and cannot. The inner one is the `key: value`
format, and indentation does nothing for it — a stored newline puts a forged
fact at the same indentation as a real one — so values are flattened before
escaping, which is what `_one_line` has always been for one section over.

**Corrected on ticket 01, having been wrong here first.** This decision
originally asked for a length-prefixed frame instead of flattening, on Hermes'
precedent, and claimed a count "closes every route, because content cannot
forge a boundary it has to count". Two things were wrong with that. Hermes'
count is load bearing because something there re-renders a restored section
and accepts it only on a byte match; nothing here restores anything, so there
was nothing to compare a count against and it was a number no code read and no
instruction mentioned. And a frame round the outside was never going to defend
a format on the inside. Ticket 01 shipped the count, both of its reviews called
it decoration, and the hole it was supposed to have closed was still open —
found by rendering a summariser-written value with a newline in it.

**The summary has an explicit cap.** A summary without one is the thing it
exists to prevent, and the cap is the only reason a summary is cheaper than the
transcript it replaced. The default is six thousand characters, which is not a
guess: DeerFlow caps its re-projected durable context at six thousand and
Hermes caps its injected memory context at six thousand head-and-tail, arrived
at independently.

**D13 — Memory splits by reader, and no new store is created for either
half.** There are two stores, both already exist, and what separates them is
who *writes*, not what kind of thing is written. The channel context file holds
what the operator authors about a room and what the summariser derives:
free-form, reload-based, and already read by the responder. The memories table
holds what an agent writes: id-addressable, soft-deleted, provenance-carrying
and already visible on the board. Both stores can carry voice material, and the
memories table carries domain material as well — which is exactly why the
*reader* split is decided by the kind on the row (D14), not by which store a
line happens to sit in. Reading this as "voice lives in the file, domain lives
in the table" is the misreading to avoid: the responder's own memory tools
write voice material into the table today.

**D14 — Memory carries a `kind`, and the reader follows from the kind.** Five
values: fact, constraint, finding, decision, voice. The first four are read by
the extractor and by later nodes; the fifth by the responder. The reader is a
function of the kind rather than a second column, so nothing has to keep two
fields in agreement. `preference` is deliberately not a kind: in this domain
every preference is either voice or a constraint, and a value that cannot be
told apart from its neighbours is a value a model will choose at random.

**D15 — Scope is two levels, and both already exist.** The shared base context
file is the global level: operator-authored, and the one layer that does not
get the per-value flatten-and-escape treatment the two untrusted layers get.
(It said "the one layer that is not escaped", inheriting a claim
`channel_base`'s own docstring had made since it was written; ticket 01 checked
and it is escaped, like everything else the seam renders. What is different is
the per-value handling, not the escaping.) The channel is the other. There is no project,
user, or conversation scope. A project scope would cross the channel boundary,
and that boundary exists because the rooms are different teams and a fact
learned in one is a leak in the next. A fact true everywhere goes in the base
file, under the operator's hand.

**D16 — Correcting a memory and replacing one are different operations.**
Update means the same claim, better worded. Supersede means the claim changed:
the old row becomes superseded and points at its replacement, and every reader
that serves a model reads only active rows. Without this, a queue that moved
from one technology to another leaves two rows and nothing to say which is
current — and the current update-in-place loses the old one entirely.

**D17 — Three new columns on memory: kind, status, superseded-by.** One
migration. Existing rows take the kind that matches their reader today and
active status.

**D18 — The two-hundred-per-room ceiling refuses; it does not evict.** This is
the current behaviour and it is correct, and it is the same doctrine the daily
token budget follows: a ceiling refuses rather than trimming. What is added is
visibility — a full room must be visible on the board and reported by the
heartbeat, because today the only party told is the model, and the only party
who can clean it up is the operator.

**D19 — Three producers, in a fixed order, and the first is mandatory.** The
operator writing by hand is first: the route that stores channel overrides
already exists, the floor is absolute, and it is the only producer that is
correct on day one. The operator confirming a candidate is second, using the
mark that already confirms a classification — this restores the approval floor
that was given up when the staged-observation tier was retired, without
restoring the tier itself, which died for want of a producer. The agent writing automatically is third and
has no floor beyond the channel scope and the operator's visibility. This board
may not ship without the first.

**D20 — A candidate memory is never read back before it is marked.** Candidates
surface for the operator; they do not sit in a tier that a prompt reads. The
tier that died was not wrong about the floor, it was wrong about having neither
a producer nor a place for a human to look. This has both.

**D21 — Memory reaches these agents by injection, not as a tool result.** The
existing rule — memory reaches a model only as a tool result — was written for
the responder, which has turn budget to spare and does not need memory on every
call. The extractor does not: it is configured for one turn plus one, and it
has already been observed spending both on skill fetches and taking its cost
from 2,256 to 10,249 input tokens. A tool-result memory would make it pay a
turn to search, and would make retrieval depend on the model inventing a query
that matches an AND-over-words filter. Injection through the existing memory
section builder does not reopen the incident that rule was written for: that
was raw concatenation into instructions, and this builder escapes at the seam.
The responder keeps its four tools and additionally receives voice memory by
injection.

**Injected memory goes into the per-call input, never into instructions**, and
this is the load-bearing half of the decision rather than a detail of where the
section is rendered. Two reasons pointing the same way. It preserves what the
tool-result rule was protecting: text that arrives in one call's input cannot
rewrite the prompt of every later call the way a promoted note once could.
And it demotes the authority of the text: memory is derived from what people
wrote, so it belongs on the channel a model reads as somebody speaking, not on
the channel it reads as its own operator. DeerFlow states this outright —
memory rides as a user-role message and never a system one, "so
user-influenceable content does not gain system authority" — and its wider
formulation is the sharper statement of what this repo calls escaping at the
seam: every string entering a model's context has a source, and the source's
trust level decides its channel. The corollary both projects record is worth
keeping in view: natural-language injection survives tag escaping, so the
channel matters even when the escaping is correct.

**D22 — The memory section builder is this board's first caller.** It was
written speculatively with three slots, lost one when the staged tier was
retired, and has had no caller since. Its two remaining slots are exactly the
two halves of this design: the conversation summary and the room's facts.
Calling it is the consumer it has been missing.

**D23 — Triage's prompt loses the skill catalogue, and node 0 stops re-paying.**
Two changes that belong to this board because both are context. No test pins
the catalogue to triage; it is 44% of that prompt and it silently tripled the
agent's turn budget. And node 0 is excluded from the checkpoint by design, so
it re-runs every pass — correctly, because it must see new messages — but it
must compare before calling a model, so an unchanged task does not re-bill an
identical extraction.

**D24 — This board reverses ticket 26, and says so.** The unbounded relevance
window was chosen deliberately, for prompt-prefix stability, and three tests
assert it. What it bought is real; what it cost is 2,552 characters of
transcript in the highest-volume prompt and a hallucinated colleague. The three
tests are rewritten one level up — a summary that is stable between calls when
the room has said nothing new — so prefix stability is still asserted, at the
layer where it is now true. No embedding model and no vector store: the ceiling
is two hundred rows per room, and the store's own comment on its keyword match
already says to revisit only if a room outgrows a linear scan.

**D25 — A candidate memory shaped like an instruction is refused, in code.**
A memory is read back as a statement of fact by a run that has none of the
context that produced it. A line that reads as a directive — send without
approval, always reply in English, skip the validation — is therefore an
instruction with a long life and no author present, and it is the most
dangerous row this board can create. The write path refuses it: deterministic,
no model, applied to every producer including the operator's own hand, because
an operator writing a rule into a memory store means the rule lives where
nothing enforces it and nothing reviews it.

Both prior arts found this hazard and neither closed it. DeerFlow has a
deterministic gate that rejects any fact not labelled descriptive rather than
prescriptive — its own comment is that an instruction cannot be stored as a
memory — but the gate sits only on the passive extraction path, and the tool
that adds a memory builds its candidate without labels and never calls it.
Hermes puts memory in the system prompt and mitigates in prose, instructing the
model to write "declarative facts, not instructions to yourself", because
"imperative phrasing gets re-read as a directive in later sessions and can
override the user's current request". One is a gate with no caller; the other
asks the model to police itself. A refusal at the single write path is stronger
than either, and it is cheap, because every producer in D19 goes through it.

**D26 — Context is gathered by one function per family, and what it returns
is content.** Added 2026-09-10, after tickets 07–10 had landed, from reading
what they left behind rather than from the original session. D3 named two
builds and D4 said what a builder returns, and neither was ever built as a
thing: the light build is a lookup inside `Triage.decide`, and the full build
is split across two places — node 0 fetches the transcript (with its budget
and cooldown), the extractor's own `would_ask` fetches the room, the domain
memories and the open questions. Loading a room's facts is gathering context,
the same as loading the transcript is, and there is no single point at which
"what did this agent see, and why" can be logged, inspected or debugged.

So: each prompt family that gathers anything has exactly one gather function,
beside its prompt module, returning one frozen value. The function reads; it
never writes, never imports a section builder, never constructs a section,
never joins. The family's `build_input` takes that value and nothing else.
The one write on the full path — recording that a compaction was ineffective
— stays in node 0, decided from a field the value reports, so a builder can
be called from anywhere (a fingerprint, a test, a probe) without a side
effect.

**No shared base class and no shared shape across families.** Ticket 45
deleted exactly that — one dataclass with a fixed shape for every family —
and D4 already records why: the families diverge, and a shape shared across
them becomes the thing every change has to fight. What is shared is the
convention — one `context.py` per family, one value, one log line of what it
gathered (counts and sizes, never content) — and a test that lists the gather
modules by name rather than deriving them, the lesson ticket 15 of the first
board left when a derived module name let a test silently stop checking.

**DAG-ready by construction, not DAG-resident yet.** Story 44 wants the full
context readable from the run's own state by a later node, and the value
D26 describes is exactly what would go there: frozen, data only, no store
handle, no rendering — and node 0 is the graph's entry, which the pool
already keeps out of the checkpoint, so context under its key would never be
persisted, which is right for derived state that is rebuilt every pass. But
no second node exists today, and writing the value into state with nothing
to read it is the seam-without-a-consumer D2 forbids. So tickets 14 and 15
build the values and stop there. The next piece of DAG work adds the one
line in node 0 that puts `FullContext` into state *and* the node that reads
it, in the same ticket. Two constraints that ticket inherits: a later node
must never copy the context into its own output, since those outputs are
persisted as JSON; and node 0 returns one value today, read by
`prepared_ok`, so the placement is either a composite output or an engine
change, decided then. `LightContext` never goes into a graph at all —
triage runs before a task exists.

## Testing Decisions

A good test here asserts what an agent was shown, not how the code decided to
show it. The observable behaviour of this feature is the content of a prompt
and the content of a `BuiltContext`, so those are what the tests read. A test
that asserts a private helper was called, or that a builder produced a
particular internal shape on the way to the same prompt, is testing the
implementation and will make the next refactor look like a regression.

Four seams, of which one is new.

**The store, tested directly against a real database.** The new read methods —
the conversation summary for a room, the memories for a given reader, the
artifacts for a task, the unanswered questions for a conversation — are tested
the way the existing relevance filter is tested: build the schema from the
models, insert rows, call the method, assert on the returned domain objects.
Prior art is the existing context-relevance test file, which is nine tests over
one store method and is the closest thing in the repo to what these need.

**The context builders, returning `BuiltContext`.** This is the new seam, and
it is placed as high as it can go: one function per phase, taking the store,
returning content. It is what makes the interesting assertions possible without
a model — that the budget was respected, that a superseded memory was not
chosen, that a voice memory did not reach the extractor's build, that the
unanswered questions match what was actually sent, and that a verbatim value
appears in the parameters and not in the summary. These are assertions over a
list, not over a substring of a prompt.

**Each family's prompt input, tested by calling it.** That the chosen content
actually reaches the prompt is a separate question from whether it was chosen,
and it is already tested this way elsewhere: the existing test that the
extractor's field documentation reaches its prompt calls the builder and asserts
on the string. The same shape covers the memory section rendering, the
agent-versus-reporter marker, and the deduplication.

**End to end, through the scripted model transport.** The existing seam. The
summariser already accepts an injected model for exactly this — its own comment
says a real run never passes one and a scripted one never touches the network —
so compaction needs no new seam. What the end-to-end tests assert is the
recorded prompt: that triage's system prompt no longer contains a skill
catalogue, that the extractor's build contains the room's facts, and that an
unchanged task does not produce a second identical model call.

Three further requirements, from this repo's own rules rather than from this
design.

The `ast` and grep guards on prompt assembly must stay green without being
relaxed: the one assembler, no hand-built sections, claim-and-wrap paired, no
family importing another family's prompt module, escaping at one seam. If the
context package needs to be exempted from one of these, the design is wrong,
not the guard.

Any ticket touching the classifier's prompt must run the frozen triage
evaluation set and report accuracy, the confusion matrix and the threshold
table alongside the change. The scripted transport pins wiring and says nothing
about whether the classifier is right; that evaluation is the only thing that
does, and removing 44% of the prompt is exactly the kind of change it exists to
catch.

Three of the decisions added after the first draft carry guards of their own,
and this repo's rule that a rule worth stating is worth a test applies to them
as much as to the rest. The refusal of an instruction-shaped memory (D25) is
tested at the write path with a table of lines that must be refused and a table
that must be accepted, exercised through every producer rather than through one
— a refusal that only one of three producers reaches is the failure both prior
arts shipped. The length-framed section (D12) is tested by handing the builder
stored text that tries to forge the frame, and asserting the render is refused
or the frame survives, whichever the implementation chooses — but stated as a
choice in the ticket rather than discovered in review. The ineffectiveness
breaker (D6) is tested by driving two compactions that do not reduce the build
and asserting the third is not attempted; and the loud failure of an
unevaluable budget clause is tested at startup, because a clause that degrades
quietly at run time is the exact shape this board was written to remove.

Every guard added by this board must be deleted once and watched go red before
the ticket is closed. A test that still passes without its guard was testing
nothing, and this board exists because five mechanisms were empty under a green
suite.

## Out of Scope

Reviving a multi-node graph. Every task type is a one-node graph today; the
five-node investigation for API issues was removed deliberately as a workflow
nobody had described. This board's consumers are node 0 and the responder. It
must leave the assembled context readable from the run's own state so that
later nodes can read it without a new mechanism, and it must add nothing else
for them.

Embeddings, vector storage and semantic retrieval. Ruled out by D24 for a
measured reason, not deferred vaguely: two hundred rows per room, hard-capped,
with a keyword filter whose own comment says to revisit only if a room outgrows
a linear scan.

A project, user or conversation scope for memory. Ruled out by D15.

Moving the existing channel context module into the new context package. The
naming will be slightly off for a while — a channel context module living
outside the context package — and that is knowingly accepted rather than
overlooked. It is a tidy-up with no behaviour in it and it does not belong in a
board about producers.

Moving the extractor's field documentation from its per-call input into its
instructions. Examined and decided against: all three extractors' instructions
are byte-identical today, so moving per-type text there would fork a shared
prefix, and the field block already occupies a cacheable prefix position within
the user turn. The case for the move is role hygiene, not cache economics, and
it is not worth a fork.

Removing the memory tools from the responder. It keeps them; D21 adds injection
alongside.

The board's own user interface beyond the minimum. Producer one needs somewhere
for the operator to write a room's facts, and that minimum is in scope. Anything
further about how the operator browses context belongs to the monitor board.

## Further Notes

The evidence for every claim in the problem statement is one recorded flow, and
it is worth keeping: message `1546809275299663952`, its two triage rows, its
three extractor rows and its two responder rows. The numbers in this spec —
1,008 characters of catalogue, 44% of a system prompt, 10,249 input tokens
across three calls where one would do, twenty-four transcript lines over seven
days, zero memories — were measured from those rows, not estimated.

Two prior reversals bear on this board and are named so that nobody has to
rediscover them. The staged-observation tier was retired because it had no
producer, not because its approval floor was wrong; D19 and D20 restore the
floor by a different route. And the shared context bundle was deleted because it
was a shared prompt *shape*; D4 is shared *content* with per-family rendering,
which is a different thing, and the spec says so because a reviewer would
otherwise be right to bounce it.

One pattern is worth stating outright, because it is the reason for D2. This
repo has now imported prompt structure from an external framework twice, and
paid twice. Once when a skill catalogue became part of every agent's
instructions and took 44% of the classifier's prompt for a tool it never calls.
Once when a blocking clarification instruction reached the extractor and turned
an empty extraction into a successful one, which was fixed in a commit whose
message ends "the suite was green for every minute this was wrong". A section
imported from a framework that serves one general-purpose agent must be able to
name which agent here needs it.
