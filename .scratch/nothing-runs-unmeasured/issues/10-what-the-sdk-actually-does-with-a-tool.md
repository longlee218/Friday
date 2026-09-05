# 10: What the SDK actually does with a tool

**What to build:** Five small corrections to the tools that ship today, each
one a fact about `openai-agents` that this codebase currently has wrong or has
not noticed. None is more than a few lines; four of the five are silent.

**Blocked by:** None (can start immediately)

**Decisions:** None — these are corrections, not choices

**Status:** done

## Why

Read against `openai-agents 0.22.0` in `.venv`, on 2026-09-05, because the
memory tools' shape (ticket 09) was written from this repo's own precedent and
the precedent turned out to carry a false premise.

### 1. A raising tool does not end the run

Commit `8211f54` gave `remember` this reason, ticket 08 on the other board
repeats it, and `friday/tools/memory.py` inherited it from them. It is not
true here.

**Corrected while writing this ticket:** the first draft said
`friday/tools/clarify.py` carries the claim too. It does not — a grep for the
rationale across `friday/` returns nothing outside the memory module. The
false premise survives only in a retired ticket's text and in git history, so
item 1 is small; what it leads to, item 2, is not.
`_FailureHandlingFunctionToolInvoker.__call__` (`agents/tool.py:652`) catches
`Exception`, calls `failure_error_function`, and re-raises **only** when that
is `None`. `function_tool`'s default is `default_tool_error_function`, so a
raising tool hands the model a string and the run continues:

```
@function_tool def boom(...): raise RuntimeError("db is down")
→ "An error occurred while running the tool. Please try again. Error: db is down"
```

The conclusion those two modules reached is still the right one — a specific
message beats a generic one — but the reason recorded is wrong, and in this
repo a recorded reason is load-bearing: the next person applies it somewhere
it actually matters.

### 2. That default puts unscrubbed exception text into the prompt

`str(error)` reaches the model verbatim. `Harness._settle` scrubs `last_error`
because "a provider exception can quote an Authorization header" — this is the
same leak by a route `scrub` never sees. `fetch_skill` and `read_skill_file`
read files, so an `OSError` carries a filesystem path; a store error carries
the database path. No tool in `friday/tools/` sets `failure_error_function`.

"Please try again" is also the wrong instruction for any tool that writes:
retrying a write that may have landed is how a row gets duplicated.

### 3. `strict_mode` makes every parameter required, defaults included

```
ask_clarification | required: ['question', 'clarification_type', 'context', 'options']
```

`context: str = ""` and `options: list[str] | None = None` read as optional in
Python and are not optional to the model. `options` at least has a `null`
branch in its `anyOf`; `context` has none, so the model must send a string and
nothing tells it that `""` means "nothing to add" — while the docstring says
"if that is not obvious from the question", a condition it has no way to act
on. It invents one every call.

### 4. The `ToolContext` alias hides the type ticket 07 wants

`friday/agent/harness.py` re-exports `ToolContext = RunContextWrapper`. The SDK
has a real class of that name (`agents/tool_context.py:42`), a subclass, and it
is what the runtime actually passes to every tool. It carries `tool_name`,
`tool_call_id`, `tool_arguments`, `tool_call`, `tool_namespace`, `agent` and
`run_config` — which is most of what ticket 07 is going to go and build a
mechanism for.

There is a trap attached. `function_schema.py:360` decides whether the first
parameter is the context by **identity**, not `issubclass`:

```python
if origin is RunContextWrapper or origin is ToolContext:
    takes_context = True
else:
    filtered_params.append((first_name, first_param))
```

So aliasing the name to any *subclass* — the natural move the day someone wants
an extra field — silently turns the context parameter into one the model sees
and must fill. No error; a wrong schema and a tool called with the wrong
arguments.

### 5. `tests/test_tools.py` cannot see nine of the tools

`_tool_objects()` reads `vars(module)`, and a tool built inside a factory lives
in a closure. Invisible to it: four skill tools, `ask_for_fields`, and the four
from ticket 09. The test that answers "what can the agents do?" is answering
with three of twelve, and adding four more did not turn it red.

A second test, `test_a_factory_tool_is_reachable_too`, names five of the nine
by hand — so the answer exists, split across two lists that cannot see each
other. That is the actual defect: not that the factories are unlisted, but
that the list which claims to be complete is not the list that has them.

### 6. Five of the decorator's parameters cannot be used here at all

`Converter.tool_to_openai` (`agents/models/chatcmpl_converter.py:971`) calls
`ensure_function_tool_supports_responses_only_features` before building the
tool definition, and that raises `UserError` for four things
(`agents/tool.py:1580`): `tool_namespace()`, `defer_loading=True`,
`allowed_callers`, and `output_json_schema`. `output_type` is a fifth by
consequence — it *builds* an `output_json_schema`, so it fails the same check.
Verified by running each one through the converter.

Every model call in this system goes through Chat Completions, which is a
recorded constraint and not a default. So those five are not "unavailable on
some providers" — they are unavailable *here*, permanently, by a decision
already made.

What makes it worth a ticket rather than a footnote is where the `UserError`
lands. It is raised per model call, at conversion, and `Harness._settle`
catches every exception into `last_error` and returns `None`. An agent given a
tool with `defer_loading=True` would therefore never answer again — every
call, forever — and the system's response to that is a `HandOver` and a warning
line. The one guard that costs nothing: run every declared tool through the
converter in a test.

### 7. `timeout` needs an async handler

`function_tool(timeout=...)` on a sync function raises at construction:
`FunctionTool timeout_seconds is only supported for async @function_tool
handlers`. Every tool in `friday/tools/` today is sync except the four from
ticket 09 — so ticket 01's per-tool timeout means making a tool async first,
which is a real change to `read_skill_file` and `fetch_skill`, not a keyword.

Worth recording alongside it, because it was the first thing suspected and it
is not true: a sync handler does **not** block the event loop. The SDK runs it
through `asyncio.to_thread` (`agents/tool.py:2645`). The reason `timeout` is
refused is that a thread cannot be cancelled, not that sync is unsupported.

## Acceptance criteria

- [x] The claim about ending the run is gone from every module that repeats
      it — which is `memory.py` alone, and ticket 08's text is annotated
      rather than rewritten, since a retired ticket records what was believed
      then
- [x] Every tool in `friday/tools/` gets a `failure_error_function` that
      scrubs and does not invite a retry — set once at the seam, not seven
      times, so a new tool cannot be declared without it
- [x] `ask_clarification`'s two optional parameters are honest — either the
      type admits absence, or the `Args:` text says what to send when there is
      nothing to say
- [x] `harness.py` stops using the SDK's name for the SDK's parent class:
      either alias the real `ToolContext`, or name ours something that is not
      already taken — with a comment about the identity check either way
- [x] `test_tools.py` sees factory-built tools, and its asserted list names all
      twelve
- [x] A test asserts every tool's schema carries a `description` for every
      field — the guard that makes the `docstring_style` question moot rather
      than remembered seven times
- [x] A test runs every declared tool through `Converter.tool_to_openai`, so a
      Responses-only parameter fails at test time instead of turning one agent
      into a permanent non-answerer
- [x] Each guard is deleted once and watched go red

## What it came to

**One default at the seam, not seven keywords at the call sites.**
`friday/agent/harness.py` no longer re-exports `function_tool` unchanged: it
wraps it and `setdefault`s `failure_error_function` on every tool this
codebase declares. That is what makes 10.2 unforgettable rather than
remembered — there is no spelling of `@tool` that skips it. A tool that wants
its own still passes one.

`_tool_failed` logs the exception scrubbed and tells the model only "that tool
is unavailable right now — carry on without it". No error text, and no "try
again", which was the half that mattered: the SDK's default invites a retry of
a write that may already have landed.

**Except for a `ModelBehaviorError`, which keeps the SDK's own words.** Found
by the review, not by writing the code: the invoker catches every exception,
and two of them — arguments that are not valid JSON, arguments that fail the
schema — are raised *before the body runs*. Neither reason for the replacement
applies to those (nothing was written, and the message is the SDK's string
plus the model's own arguments), and the replacement removed the only recovery
available: emit the call again correctly. It lands hardest on `classify`,
which is how triage records every decision, against a third-party endpoint
under no obligation to enforce the `strict` flag the schema is sent with — so
one wrong-typed argument became a mention on the operator's desk instead of a
retry the model could do itself.

**`ToolContext` now names the class the runtime actually passes.** It was
aliased to `RunContextWrapper`, the parent. The comment above it carries the
constraint that makes the alias narrow — the SDK's check is on identity, so it
may point at either of the two classes it names and at *nothing else*,
including a subclass of them.

**`docstring_style` is not pinned, and the first draft of this work pinned
it.** The reasoning was that auto-detection could guess wrong and drop every
`Args:` description silently, so stating the style was free insurance.
Measured after the review disputed it: `_detect_docstring_style` returns
`google` for every docstring here and falls back to `google` when it scores
nothing, so the pin changed no schema at all — while a docstring written
`:param x:` **loses** its descriptions under a google pin and keeps them under
detection. The pin could only ever break the case it was there to protect.

The mutation test that was supposed to catch this did not, and the reason is
worth more than the fix: it changed the pin to `"numpy"` and watched the
descriptions vanish. That proves the *test* can see lost descriptions; it says
nothing about whether the *pin* is doing anything, which is what the comment
claimed. Deleting a guard and watching red only works when what you delete is
the guard.

**`ask_clarification`'s two trailing parameters admit null.** `context: str =
""` was listed as required with no way to express having nothing to add, so an
agent told "if that is not obvious from the question" had to invent a sentence
every call. `str | None` puts the null branch in the schema and the docstring
now says to use it.

**The listing answers the question it was written for.** `_tool_objects()`
builds the factories as well as scanning module attributes, so the asserted
list is twelve rather than three, and `test_every_factory_is_registered_here`
fails if a new factory is not built there. `test_a_factory_tool_is_reachable_too`
went: its whole subject was that the listing could not see these, which is no
longer true, and a test whose docstring states a falsehood is the drift this
file exists to catch.

Four new guards beyond the criteria's two: the failure message, the argument
error that keeps its retry, the alias, and the factory registry.

**All six were deleted once and watched go red** — and one of them was not a
guard on the first attempt. `test_every_factory_is_registered_here` originally
matched the factory name against `inspect.getsource(_factories)`, and deleting
the call left it green, because the `import` line above still spelled the
name. It reads the AST for actual calls now. That is the exact failure the
delete-it-and-watch rule exists to find, found by following it.

681 tests pass (671 before: +11 new, −1 folded).

## What the review caught that the work did not

Both of the findings that changed code came from the subagent review required
by `CLAUDE.md`, and neither would have been found by running the suite:

1. `_tool_failed` swallowing `ModelBehaviorError` — a regression introduced by
   this ticket, in the fix for 10.2, invisible because no test covered the
   argument-error path at all.
2. The `docstring_style` pin being inert-at-best and harmful-at-worst — the
   opposite of what 10.7 and this ticket's own comments asserted, and asserted
   confidently.

The pattern in both: the change was reasoned from what the SDK's *interface*
suggested, and the review read what it *does*. The guard that was supposed to
protect the second one passed throughout, because it was aimed at the wrong
thing.

## Open, and not decided here: should triage recover from its own bad call?

Found by the review after the `ModelBehaviorError` exemption went in, and
verified through the real `Triage` with the SDK's `ScriptedModel`: the
exemption does not reach triage at all.

`Triage` builds its harness with `tool_use_behavior="stop_on_first_tool"`, and
that setting ends the run at the first tool call's **output** — which the SDK
cannot distinguish from a failure, because a `failure_error_function` return
value *is* the tool output. Scripted `[malformed classify, correct classify]`:

```
bad-then-good -> NeedsHuman('triage produced no classification')
good          -> Decided(type='api_issue', confidence=0.9)
```

The second turn is never requested. So the "please try again with valid JSON"
wording becomes the run's final output and no model reads it. Nothing is
dropped — the mention goes to a person — but it goes there for something the
model could have fixed in one more turn.

Three ways out, and choosing is the operator's, not a review finding:

1. **Leave it.** A malformed call becomes `HITL`, which is what every other
   triage failure does, and the never-drop rule holds. Costs one operator
   interruption per malformed call, at an unknown rate — nothing measures it
   today, which ticket 02 would change.
2. **Let a failed tool result not count as the final output**, keeping
   `stop_on_first_tool` for the success path.
3. **Drop to `tool_use_behavior="run_llm_again"`.** `tool_choice: "required"`
   is already set and `capture.decided` is already the terminator, so the
   machinery is there. Changes the turn budget, and `max_turns: 1` in
   `config.yaml` would have to move with it.

Whichever is chosen, `tests/test_triage.py::test_a_malformed_classify_call_ends_the_run`
is what turns red when it happens; it exists to make the current behaviour a
decision rather than an accident.

## A second review pass, and what it cost to argue with it

The review went four rounds. Findings that changed code, after the two above:

- **`Redacting` let a token through** in an exception argument and in a
  traceback — ticket 11, and the most serious thing this ticket turned up,
  found by disputing one careless sentence in a docstring.
- **The `ModelBehaviorError` exemption does not reach triage**, because
  `stop_on_first_tool` makes a tool's failure message the run's final output.
  Both the test docstring and `CLAUDE.md` claimed a benefit for `classify`
  that the code does not deliver. Corrected, and the real behaviour is now
  pinned by `test_a_malformed_classify_call_ends_the_run`.
- **`Limits` could only drift**, the thing it existed to prevent — the tool
  docstrings are static text and the factory took an override. Gone; see
  ticket 09.
- **A missing `MemoryScope` read as a store outage.** `_scope` names it now.
- **`MemoryScope` gave provenance defaults** under a docstring saying every
  field was runtime-supplied.
- **Both `ast.FunctionDef` walks were blind to `async def`**, one file after
  the memory tools made async the house style for anything touching a store.
- **The alias test argued for a property it never ran.** It asserted
  `harness.ToolContext in (RunContextWrapper, SdkToolContext)` — two names the
  test imported itself, never touching `function_schema`. Rewritten to assert
  the outcome: a tool annotated with the alias has no `ctx` in its schema. Red
  on a subclass, green on either valid identity.
- **`friday/tools/__init__.py` named four of twelve tools.** It no longer
  enumerates; it points at the asserted list, which is the only thing here
  that cannot be wrong about it.

Three of those were defects this ticket introduced. Two were pre-existing and
one of those was a credential leak. The suite was green for every one of them,
at every point, which is the argument for the review step stated as evidence
rather than as a rule.
