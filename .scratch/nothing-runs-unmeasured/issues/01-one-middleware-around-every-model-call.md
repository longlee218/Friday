# 01: One middleware around every model call

**What to build:** A single wrapper inside `Harness._settle` that every model
call passes through — timeout, budget check, retry decision, recording — so
none of the four call sites has to remember any of it.

**Blocked by:** None (can start immediately)

**Decisions:** D1, D2

**Status:** todo

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

- [ ] `_settle` wraps `Runner.run` in a timeout, configured per agent, with a
      default that is not the provider SDK's ten minutes
- [ ] Every tool that reaches outside this process carries its own `timeout`,
      and the `timeout_behavior` for each is chosen rather than defaulted —
      note that this requires an **async** handler (ticket 10.7), so the sync
      skill tools have to be converted before they can have one
- [ ] A timeout is a `last_error` like any other failure — no exception escapes
      the harness, the rule this module already states
- [ ] The harness takes a recording sink at construction; `run(calls=...)`
      either goes away or becomes a second sink, and no production call site
      passes one
- [ ] Every agent's calls reach the sink — triage, all three extractors, the
      responder, the summariser — asserted by a test that enumerates them
      rather than deriving the list
- [ ] The composition root is the only place the sink is built
- [ ] Each guard is deleted once and watched go red
