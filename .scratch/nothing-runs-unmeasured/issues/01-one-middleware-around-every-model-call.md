# 01: One middleware around every model call

**What to build:** A single wrapper inside `Harness._settle` that every model
call passes through — timeout, budget check, retry decision, recording — so
none of the four call sites has to remember any of it.

**Blocked by:** None (can start immediately)

**Decisions:** D1, D2

**Status:** done

## Why

`friday/agent/harness.py:226` is already the only door: `Runner.run` is called
in exactly one place, and `tests/test_harness.py` fails if a second module
reaches past this file. What it does today is run the agent, swallow the
exception into `last_error`, and attach `LogHooks`. Everything else this board
needs — a ceiling, a clock, a retry, a record — has nowhere else to go that
would not have to be repeated per caller.

`AgentHooks` cannot do it (D2). `on_llm_start` fires after the decision to
spend has been made and `on_llm_end` after the money is gone; neither can
refuse a call or bound one.

The `calls=` parameter is the evidence for D1. It is optional, and
`friday/extraction/__init__.py:79`, `friday/memory/channel_context.py:280` and
the `Pool` → `Responder.draft` path all omit it. An observability seam that a
caller can forget is one that will be forgotten.

## A tool's clock is not the model's clock

Added after reading the SDK on 2026-09-05 (ticket 10 is the rest of that read).
`function_tool` takes `timeout` and `timeout_behavior` — a per-invocation
bound on the tool itself, separate from anything wrapped round `Runner.run`.
The two are different failures and the middleware only covers one: a model
that never answers stalls the run, and a Loki query that never returns stalls
it just as completely from inside a turn the middleware has already entered.

`timeout_behavior` chooses which kind of failure it is — `"error_as_result"`
hands the model a timeout string and lets it carry on, `"raise_exception"`
fails the run. For a tool an agent can work around, the first; for one whose
absence makes the rest of the turn meaningless, the second. That is a
per-tool decision, so it belongs on the tool, not in this middleware.

## Acceptance criteria

- [x] `_settle` wraps `Runner.run` in a timeout, configured per agent, with a
      default that is not the provider SDK's ten minutes
- [~] Every tool that reaches outside this process carries its own `timeout`
      — **not applicable yet, and deliberately not forced.** No tool reaches
      outside this process today: `SkillLibrary.build()` calls `.load()` once
      at startup and reads every skill and its extra files into memory, so the
      four skill tools are dictionary lookups, and `mcp_servers` is empty.
      Adding a timeout to a dict lookup — and converting four sync handlers to
      async to be allowed to — is speculative work for a failure that cannot
      happen. The requirement moves to where it will bite: ticket 07 already
      says an MCP tool has to be wrapped in a function tool of ours to be
      bounded at all, and that wrapper is where the clock goes
- [x] A timeout is a `last_error` like any other failure — no exception escapes
      the harness, the rule this module already states
- [x] The harness takes a recording sink at construction; `run(calls=...)`
      either goes away or becomes a second sink, and no production call site
      passes one
- [x] Every agent's calls reach the sink — triage, all three extractors, the
      responder, the summariser — asserted by a test that enumerates them
      rather than deriving the list
- [x] The composition root is the only place the sink is built
- [x] Each guard is deleted once and watched go red

## What it came to

**The sink is a constructor argument.** `Harness(record=...)` takes an async
callable of one `ModelCall`, and `_settle` hands it every call the run made,
in a `finally` — a call that failed still cost what it cost, and a run that
timed out has usually already had one whole exchange with the provider, which
is exactly the run somebody will want the prompt of. A sink that raises is
caught and logged: a failed write loses a row, a raised write would lose an
answer already paid for.

`run(calls=...)` is gone. What a caller still names is `message_id`, and the
difference is the point: forgetting it loses a correlation key, not the
record. `TriageRunner._decide` no longer drains a list and writes it — that
loop is why triage was the only agent whose prompts were ever kept.

**The clock is `asyncio.wait_for` around `Runner.run`,** at
`timeout_seconds` per agent, 60s by default. `AsyncOpenAI` is constructed with
no timeout and the client's own default is ten minutes with retries; the pool
works one task at a time, so an unbounded run stalls every task while the
heartbeat reports the process alive. A timeout is a `last_error` like any
other failure, and `_why` writes the reason out because
`asyncio.TimeoutError` has an empty `str()` — without it the operator reads
"triage failed: " and nothing else.

**The composition root builds the sink and nothing else may.**
`record_call` closes over the database in `run_agent._run`, and a test fails
if any module under `friday/` other than the store reaches for
`record_model_call`. A second test names the four builders — extractors,
triage, responder, summariser — and fails if any is constructed without a
sink. Written out, not derived, for the reason CLAUDE.md gives: a list that
computes itself agrees with whatever the code happens to be.

Six guards, each deleted once and watched go red. The timeout one is worth a
line: with `wait_for` removed the test takes 31 seconds and then fails, which
is the failure it exists to prevent, demonstrated.

693 tests pass (682 before, +11). `uv run mypy friday run_agent.py` reports 32
errors before this change and 32 after — none in what it touched. mypy is in
the dev group but is not configured, wired to anything, or recorded in
CLAUDE.md, and this work did not add it.

## What the review changed

Four defects, all introduced by this ticket, none caught by a green suite.

**A single-turn timeout recorded nothing** — the exact case the `finally` was
written for, and the comment above it said the opposite. `LogHooks` builds its
`ModelCall` in `on_llm_end`, and a timeout cancels the run inside the provider
request, so that hook never fires. The claim that "a run that times out has
usually already had one whole exchange" is true only of multi-turn agents, and
`max_turns: 1` is triage, all three extractors and the summariser — every
agent this matters most for. `LogHooks.unfinished()` now returns what
`on_llm_start` knew, with an empty output and no usage rather than a zero that
reads like a measurement.

**All four builders could drop their forwarding line with a green suite.**
D1's own failure mode, one layer down: the `calls=` list was forgettable at
the call site, and `record=record` is forgettable in the builder. The
criterion asked for a test that enumerates the agents and no such test
existed — the one written here checked the composition root's *call sites*,
which is a different thing. `tests/test_recording_reaches_every_agent.py`
drives each builder through its public entry with a sentinel and asserts the
`Harness` it constructs was handed that object. All five forwarding lines go
red when deleted; the summariser has two, and the first version of that test
covered only the second, which is why it is worth saying that a mutation that
does not turn a test red is the test's problem and not the mutation's.

**`test_every_agent_is_built_with_somewhere_to_record` passed when nothing
recorded.** It asked whether the keyword was spelled, never what it was
handed, so `record=None` at all four sites — the whole system recording
nothing, which is the bug this board exists to fix — went straight through it.
It now requires a `Name`, and requires all four to name the same sink.

**The board lost calls it used to show.** Not a defect in the middleware but a
consequence of it: `friday/board/__init__.py` and `friday/ops/api.py` both
built `{c.message_id: c for c in await db.model_calls(limit=200)}`, and until
today every row in that window was triage's and carried a message id. With
four agents writing, a page of the most recent calls can be entirely rows that
can never match a message while the call that classified it sits just outside
the window. `Database.calls_by_message` asks for the calls belonging to the
messages being rendered, which is what the board meant in the first place.

## A second review round

**The board eviction was real and is fixed** — the finding above. Two more
came back after it, and only one needed code.

**Cancellation drops the rows it has not written yet, and that is the
intended trade.** `_write_down` awaits inside the `finally`, so a cancel
delivered mid-write raises `CancelledError` at that await, which
`except Exception` does not catch: the loop stops and the rest are lost.
Measured — a cancel during an in-flight write writes nothing. Widening the
catch would swallow the cancellation and keep a shutting-down process writing
rows, which is the worse failure: the caller has already stopped waiting for
the answer those rows describe. Left as it is, said out loud in the docstring,
and pinned by a test that turns red the moment somebody "improves" the catch
to `BaseException` — because that change looks like a fix and quietly turns
Ctrl-C into a process that will not stop.

**Every row the three new agents write has a NULL `message_id`, and nothing
can read them back.** `db.model_calls(message_id=None)` means "no filter",
not "the uncorrelated ones", and the only per-call endpoint filters by
message — so the extractor, responder and summariser prompts are stored and
reachable nowhere but the SQLite file. Not fixed here: correlation keys are
ticket 02's subject, and it has gained the criterion. Worth stating plainly
though, because this ticket's own claim is weaker than it reads: every agent's
calls now reach the *store*; three of the four do not yet reach the *operator*.

694 tests pass.
