# Spec: every answer has a shape

Status: ready-for-agent. Reached on 2026-09-11 by following one mention through
the running system, then probing the configured provider directly rather than
reading about it. Every claim below that concerns provider behaviour was
measured; the measurements are quoted where they decide something.

## Problem Statement

Two different things in this system are wrong in the same way: nothing checks
what a model actually said before the system believes it, and what a model
said arrives by a route nobody can follow.

**Nothing checks the shape.** An agent's answer is turned into structure by
whatever the caller happened to write. `friday/extraction/` scanned for braces
and then fell back to scraping `key: value` out of prose; the summariser called
`json.loads` on the whole reply. Neither checked a single type. A dataclass
constructor accepts `environment=["a","b"]` without complaint, so a wrong-typed
answer built a `Params`, reached `validate`, and surfaced as `unhashable type:
'list'` — a Python error quoted at the operator inside a hand-over. An
unreadable answer became `{}`, and since every `Params` field has a default, an
empty extraction was indistinguishable from a successful one: the reporter was
asked for what they had already written.

Part of that is now fixed — `Harness.run_structured` validates a written answer
against a dataclass and gives it one correction turn. But it gets the JSON by
**scraping it out of a text blob**, because that is how the answer arrives.
Which is the second problem seen from the other side.

**The answer travels by a side channel.** Where an agent answers with a tool
call, the tool's body writes into an object hung off the SDK's per-run
`context` — `ClassifyCapture.decided`, `FieldsCapture.clarify` — and the caller
reads it back out afterwards. The model's answer is therefore not a return
value of anything. To follow "what did triage decide", a reader has to know
that a tool wrote into a capture, that `stop_when` watched that capture, and
that the runner read it after the call returned. Nothing in a signature says
so. It is also two mechanisms for one job: the same `context` parameter carries
`MemoryScope`, which is genuinely per-run *state* and exactly the right use of
it, so the one slot means "who is this run about" for one agent and "where the
answer will appear" for another.

**And the same identity is rebuilt at every layer.** Which channel, which
message, who wrote it, which task, what it replies to — these travel as
separate keyword arguments through `Triage.decide`, `Responder.draft`,
`prepare`, `Extractor.run`, `MemoryScope`, and the recording sink's
`message_id`/`task_id`/`node`. Each layer names the subset it needs, so adding
one more fact means threading one more parameter, which is what ticket 08 and
ticket 15 of `what-the-room-already-knows` had already been paying: `known`
threaded through five signatures before it collapsed into one object.

## Solution

**An answer is a value with a declared shape, returned by the seam that made
the call.** One method on `Harness` asks a model for a shape and hands back an
instance of it, or `None`. The shape is a dataclass. It is described to the
model from its own fields, the model answers through a tool built from the same
dataclass — so the JSON arrives in the protocol's own field rather than mixed
into prose — and the arguments are validated against the dataclass before
anybody sees them. An answer that does not fit earns one correction turn,
carrying the reason. Nothing is read off a capture; no caller parses anything.

**One state travels the whole way.** `FridayState` is what a message's journey
carries: the provider, the channel, the thread, the message, who wrote it, what
it replies to, and the task it became. It is what `context` means from now on —
per-run state, and only state — and it reaches the memory tools, the recording
sink, and anything later that needs to know which room it is in. Its fields are
read-only; a step that learns something new calls a named method and gets a new
state back, so every change is one place, on purpose, and greppable.

**Together they close the loop.** Where the answer is a tool call, there is no
text to scrape, so the string-scraping path stops being the main road and
becomes the fallback for a provider without tool support.

### What was measured, and what it settles

Probed against the configured provider (MiniMax-M3, `api.minimax.io/v1`), on
2026-09-11:

- **`response_format: {"type": "json_schema", "strict": true}` is accepted and
  ignored.** No 400 — which is the failure `docs/DESIGN.md` feared and told
  somebody to verify, and nobody had. The reply came back inside a ```json
  fence, after a `<think>` block, with prose following, naming an enum member
  that was not in the enum it had just been given. A provider that rejects the
  parameter is one you find out about; one that accepts and ignores it leaves a
  schema in the code that reads like a guarantee.
- **A written answer is never clean.** `<think>` block: yes. Code fence: yes.
  `json.loads` on the reply: fails.
- **A tool call is clean.** The reasoning still appears — in `content` — while
  the data arrives in `tool_calls[].function.arguments`: no `<think>`, no
  fence, `json.loads` succeeds, and a `curl` carrying `{"a":1}` came back
  byte-for-byte. Two different fields, and only one of them is read.
- **Neither surface constrains the model.** Tool calling is not enforced on the
  wire either: asked to, the model returned `task_type: "hardware_issue"`
  against a closed enum. What makes the tool path safe is that the **SDK
  validates arguments client-side** with pydantic and hands a malformed call
  back to the model. That is the mechanism this spec generalises.
- **The SDK's `output_type` cannot be used.** It emits exactly that
  `response_format` envelope for Chat Completions with no prompt-only mode,
  `strict_json_schema=False` only flips a boolean inside the same envelope, and
  its validation is gated behind the same `is_plain_text()` flag as the wire
  format. There is no way to ask it for "validate locally, send nothing extra".

## User Stories

1. As the operator, I want a model's answer checked against a declared shape before anything acts on it, so that a wrong-typed value fails where a model caused it rather than three layers away.
2. As the operator, I want an answer that does not fit to be refused rather than guessed at, so that nothing downstream is built on an interpretation nobody chose.
3. As the operator, I want a model that answered badly to be asked again once, with the reason, so that one malformed reply does not cost a task.
4. As the operator, I want a model that answers badly twice to stop costing money, so that a bad day does not become an expensive one.
5. As the operator, I want "no usable answer" to be a different value from "the model found nothing", so that an empty extraction is never mistaken for a successful one.
6. As the operator, I want the reporter never asked for what they have already written because a parser could not read a reply.
7. As a developer, I want the answer to be the return value of the call that asked for it, so that reading the code tells me where an answer comes from.
8. As a developer, I want no tool to write its result into a per-run object the caller reads back, so that there is one way an answer travels and not two.
9. As a developer, I want the shape described to the model generated from the same declaration the answer is validated against, so that the two cannot drift.
10. As a developer, I want a field's meaning to live on the field, so that the prompt text and the validation read the same source.
11. As the operator, I want the answer to arrive in the protocol's own field rather than inside prose, so that no reasoning block or code fence has to be parsed away.
12. As the operator, I want string-scraping kept only as the fallback for a provider that cannot do tool calls, so that the common path has nothing to guess about.
13. As the operator, I want triage's decision validated against the task types this system actually supports, so that an invented type never opens a task.
14. As the operator, I want `skip` to be one of the values in that same closed set, so that "there is no work here" is validated exactly as strictly as "there is".
15. As the operator, I want an invalid classification stopped at triage, so that it does not reach the pool and spend a cycle discovering it has no graph.
16. As the operator, I want the extractor's fields validated against the task type's own parameter schema, so that a value that cannot be a string never becomes one.
17. As the operator, I want the extractor's request for more detail to be part of its answer rather than a separate tool call, so that one call produces one validated object.
18. As the operator, I want the fields the extractor asks about checked against the fields that type actually has, so that it cannot ask for something that does not exist.
19. As the operator, I want the summariser's answer validated the same way as everything else, so that a reply that is not a summary is never stored as one.
20. As the operator, I want a room's stored summary never to contain a model's reasoning block, so that no later prompt for that room is built on it.
21. As a developer, I want one way to ask a model for structured JSON, so that the next agent that needs one does not invent a third parser.
22. As a developer, I want a single state object carried through a message's whole journey, so that adding one fact does not mean threading one more parameter through five signatures.
23. As a developer, I want that state to carry which provider, channel, thread, message, author, reply and task a run is about, so that any step can answer "where am I" without being told again.
24. As a developer, I want that state's fields to be read-only, so that nothing changes it from a distance.
25. As a developer, I want every change to that state to go through a named method, so that "what can change, and where" is a list I can read.
26. As a developer, I want each of those methods to return a new state rather than mutate in place, so that a value handed to one step cannot be changed underneath another.
27. As a developer, I want the memory tools to read their scope from that same state, so that there is one notion of "which room is this" rather than two.
28. As the operator, I want a memory still written against the room and task the run is about, so that this change alters nothing about who can read what.
29. As the operator, I want the recording sink to keep naming the message, task and node a call was about, so that the board's correlation does not regress.
30. As a developer, I want the state to be the only thing the SDK's per-run context carries, so that the one slot means one thing.
31. As a developer, I want dead capture machinery deleted rather than left for a caller that never came, so that the next reader is not told about a door that is not in the room.
32. As a developer, I want the tool that asks for fields deleted along with the capture it wrote into, so that removing the side channel is complete rather than partial.
33. As the operator, I want the change verified against the real provider rather than assumed, so that a design is not built on a fear nobody checked.
34. As a developer, I want the measurement written down beside the decision it settled, so that the next person does not re-derive it or re-fear it.
35. As the operator, I want the classifier's accuracy measured against the frozen evaluation set before and after, because triage's prompt and its answer's shape both change.
36. As a developer, I want the tests driven through the scripted model transport rather than a hand-written harness double, so that a test cannot pass while skipping the mechanism it is about.
37. As a developer, I want every new guard deleted once and watched go red, because the guard that was only written down is the one that drifted.
38. As the operator, I want a correction turn counted against the run's turn budget, so that "ask again once" cannot quietly become "ask again forever".
39. As the operator, I want the worst-case cost of a structured call stated plainly, so that a doubled timeout is a decision rather than a surprise.
40. As a developer, I want the responder's free-text answer left alone, because prose is its actual output and a shape would be a lie about it.

## Implementation Decisions

**D1 — One method asks for a shape, and returns it.** `Harness` gains a single
entry point for a structured answer. It takes the prompt, the shape, and the
state; it returns an instance of the shape or `None`. `None` means nobody got a
usable answer, which is what `run()` already means by it, and callers already
turn that into their own kind of work. It never means "an answer with nothing
in it".

**D2 — The shape is a dataclass, and it is the only declaration.** The prompt's
description of the shape is generated from it, the validation is against it,
and the returned value is an instance of it. A field's meaning lives on the
field as `doc` metadata, beside the type. There is no second place that lists
the fields — that is the drift this replaces, where the summariser carried its
four keys in prose *and* in a tuple.

**D3 — The answer arrives as a tool call, built from the shape.** The tool's
parameter schema is generated from the dataclass; the model is required to call
it; its arguments are validated and the instance becomes the run's final
output. Measured reason: a tool call's arguments arrive in their own protocol
field, free of the `<think>` block and code fence that every written answer
from this provider carries.

From a prototype, the two mechanics that are not obvious and decide the design:

```python
# The tool RETURNS the problem; it must not raise. A raise is wrapped in
# UserError and the whole run fails — the model never gets to correct it.
async def invoke(ctx, args: str):
    try:
        return adapter.validate_python(json.loads(args))
    except (json.JSONDecodeError, ValidationError) as bad:
        return f"that did not fit: {bad}"

# "Answered" means the tool returned an instance, not that it returned
# anything — the same distinction `stop_when` already draws, for the same
# reason: an error string is a tool output too.
def is_final(ctx, results):
    out = results[-1].output
    if isinstance(out, schema):
        return ToolsToFinalOutputResult(is_final_output=True, final_output=out)
    return ToolsToFinalOutputResult(is_final_output=False)
```

**D4 — The correction budget is the turn budget.** A malformed call comes back
to the model as the tool's output; `max_turns` decides how many corrections it
gets, which is the mechanism the SDK already applies to every other tool and
the one CLAUDE.md documents as "one turn, and it is `max_turns`". Exhausting it
raises inside the SDK, `Harness` turns that into `last_error` as it does every
other failure, and the caller gets `None`. There is no second retry loop.

**D5 — Validation is local, and nothing is sent on the wire.** No
`response_format`, because it is accepted and ignored by the configured
provider, and a schema nobody enforces reads exactly like one somebody does.
The tool's parameter schema is sent because that is how a tool is declared, but
nothing relies on the provider honouring it: the arguments are validated in
this process, every time.

**D6 — Triage answers one closed set, `skip` included.** The two tools become
one shape: a decision and a confidence, where the decision is an enum built
from the registry of task types plus `skip`. This **reverses** the split
recorded in CLAUDE.md — "everything `classify` names opens work, and `skip`
names the absence of it" — and the reversal is the point: the two were
validated differently because they were different tools, and an invented type
could reach `TriageRunner._apply` and open a task the pool then discovers has
no graph. One enum, generated from `PARAMS`, validated once, before anything is
opened. What the old split was protecting — that `skip` opens nothing — is
`_apply`'s business and stays there.

**D7 — The extractor's request for more detail is part of its answer.** The
shape is that task type's own parameters plus what it wants to ask about and
why. `ask_for_fields` and the capture it wrote into are deleted. The fields it
may name are validated against the type's own field names, so it cannot ask
about something the type does not have — which the tool's enum did, and which
must not be lost in the move.

**D8 — `FridayState` is the run's state, and the only thing `context` carries.**
It holds what a message's journey knows: provider, channel, thread, message,
author id and name, what it replies to, the task, and which agent is running.
It reaches the memory tools, and it is what the recording sink reads the
message, task and node from.

**D9 — `FridayState` is read-only, and changes go through named methods.**
Fields are not assigned from outside. A step that learns something — the task a
message became, the message a draft is about, which agent is now running —
calls a method that returns a new state carrying it. Every legal change is
therefore a named, greppable method rather than an assignment anywhere, which
is what keeps "what can change this" answerable. A frozen dataclass with
`replace`-based methods is the shape; the methods are the interface.

**D10 — `FridayState` replaces `MemoryScope` everywhere.** The store's memory
methods take the state; the memory tools read channel, task, agent and message
from it. One notion of "which room is this", not two. `MemoryScope` is deleted
rather than kept as an alias, because a second name for one thing is how the
two drift.

**D11 — The summariser uses the same method.** Its shape already exists; what
changes is that its answer arrives and is validated the same way as every other
structured answer, rather than through its own parser.

**D12 — The responder is not touched.** Its output is prose, which is its
actual product; giving it a shape would be a lie about what it returns. It
keeps its memory tools, and those tools now read `FridayState`.

**D13 — Scraping survives as a fallback, not the road.** The text-finding and
validating helpers stay: a provider without usable tool calling, and any answer
that does arrive as text, still needs them. What changes is that they are no
longer how the two main agents get their answers.

**D14 — Dead capture machinery goes.** The clarification tool and capture with
no caller are deleted in this work rather than left "kept for one", because
every reader who meets them has to work out that nothing calls them.

**D15 — Nothing about who may read a memory changes.** Scope is still supplied
by the runtime and never named by the model; a channel's memory is still
invisible to a run in another one. This work changes the object that carries
the scope, not the rule.

## Testing Decisions

**A good test here asserts what a caller observes** — the value returned, the
task opened, the row written — never that a particular capture was populated or
a particular parser was called. That distinction is the subject of this spec:
the tests that survive it should be the ones that never knew how the answer
travelled.

**The seam is the scripted model transport, through a real `Harness`.** The
repo already drives agents this way (`tests/test_channel_context.py`,
`tests/test_harness.py`, `tests/test_extraction.py`): a `ScriptedModel` returns
the tool calls or messages a real provider would, and everything between the
model and the assertion is production code. This is the highest available seam
and the right one — it exercises tool declaration, argument validation, the
correction turn and the final-output handling, none of which a hand-written
harness double would touch. **A double that supplies its own structured-answer
method must not be used**, for the reason `tests/conftest.py`'s
`ScriptedHarness` already records: a stub with its own copy of the mechanism
lets a test pass while skipping the thing it is about.

Four groups, at three seams:

1. **The shape seam** — the structured-answer method on `Harness`, driven by a
   scripted model. Asserted: an answer that fits returns an instance and costs
   one call; one that does not is corrected once and then returns `None`; the
   correction carries the reason and names the field; a failed reply is not
   quoted back to the model; no usable answer is `None` rather than an
   all-defaulted instance. Prior art: `tests/test_structured.py`.
2. **`FridayState`** — a pure value, tested directly. Asserted: fields cannot
   be assigned; each named method returns a new state with exactly one thing
   changed and the original untouched; the state a memory write lands under is
   the one the run carried. Prior art: `tests/test_memory_store.py` drives the
   store with a scope directly.
3. **Agent outcomes** — triage and extraction, at the seams that already exist.
   Asserted: a decision outside the closed set never opens a task; `skip` opens
   nothing; a wrong-typed field is refused rather than constructed; a
   clarification names only fields the type has; an extraction that produced
   nothing is not remembered as an answer. Prior art: `tests/test_triage.py`,
   `tests/test_extraction.py`, `tests/test_dag_prepare.py`.
4. **The tool inventory** — the asserted list of tools shrinks as the answer
   tools are deleted. Prior art and guard:
   `tests/test_tools.py::test_the_tools_this_system_has_are_all_in_one_place`,
   which is a written-out list and will fail until it is updated deliberately.

**Every new guard is deleted once and watched go red** before it is believed.
This spec's own history is the argument: the first version of the validation
work reintroduced the exact bug it was written to delete — an object of
entirely unknown keys filtered down to `{}` and validated as a success — and
the tests as written did not catch it.

**The classifier evaluation is owed.** Triage's prompt and the shape of its
answer both change, so `evals/run_triage_eval.py` runs against
`evals/triage.jsonl` before and after, with the accuracy, confusion matrix and
threshold table reported. CLAUDE.md's verifying-a-change rule names this
explicitly, and this is the first work in a while that genuinely triggers it.

## Out of Scope

- **The responder's output.** Prose is what it produces; see D12.
- **Whether the provider should be MiniMax.** The measurements say what this
  one does; changing it is a different decision.
- **Retry, timeout and budget policy.** A correction turn is a turn, under the
  existing `max_turns`; nothing here changes `timeout_seconds`, the attempt
  policy, or the daily ceiling.
- **Writing `FridayState` into `DAGState`.** The board
  `what-the-room-already-knows` deferred putting a context value into graph
  state until a second node exists to read it, and that reasoning is unchanged:
  a value in state with no reader is the seam-without-a-consumer D2 of that
  board forbids.
- **Any new task type, graph node, or memory kind.** This work changes how an
  answer is obtained and how state travels, and must change nothing about what
  the system decides.
- **The board UI.** No route's response shape changes.

## Further Notes

**This reverses two recorded decisions, and both reversals are deliberate.**
The `classify`/`skip` split (D6) and the extractor's separate clarification
tool (D7) were each written down with reasons. The reasons were about how the
model is *addressed*; the problem they now cause is about how its answer is
*validated and carried*. CLAUDE.md's own rule — correct it in the same commit —
applies to both.

**The work already committed is the fallback, not waste.** `find_json`, `fits`
and `describe` were built and tested against the measured provider output, and
they remain: `describe` generates the prompt text for the tool's own shape, and
`fits` is the validation the tool body performs. What changes is that the
scraping path stops being how triage and the extractor get their answers.

**The largest risk is the turn budget.** The extractor runs on one turn plus
one, and a correction turn spends one. Whether a correction and a skill fetch
can both fit inside one extraction is the thing to watch when this lands; the
answer may be that the extractor's budget needs a number of its own, and that
should be a decision taken on evidence rather than pre-emptively.

**The second-largest is the evaluation.** Triage is the highest-volume path
here, and this changes both its prompt and the shape it answers in. The eval
exists for exactly this; a regression in it is a reason to stop, not a detail
to note.
