# 12: Flow screen — state per step

**What to build:**

The Flow screen today renders one card per model call, with the agent
name, model, latency, and a few pills (tokens, attempt). What the
operator cannot tell from the screen is **what each step is doing
right now**: every card looks the same, and a "stuck" call looks
indistinguishable from a "succeeded" one until it is over.

Ticket 12 makes state explicit. Each step — every turn (a model call
and the tools it reached for) and every tool call on its own — carries
a state the screen renders next to the agent or tool name.

**State per step, derived from what is already stored:**

- A **turn** is `done` when every model call and every tool it
  reached for succeeded; `failed` when any of them did; `retrying`
  when the call's `attempt > 1`; `waiting` when the call ended in a
  handover (`Ask`/`HandOver`).
- A **tool call** is `ok` when `failed = false`; `failed` when it is.
  The wire already carries both, the screen just did not render them.

**Frontend:**

- `FlowScreen.tsx` reads `state` off each turn and each tool call and
  renders a small pill (`Pill` with tone `good` / `warn` / `bad`)
  alongside the agent or tool name. The state word, not the colour,
  is the truth — colour is decoration, the audit's rule.
- The state pill replaces the existing generic "attempt > 1" warn
  pill on a model call, which was the only state the page carried
  before. That signal is folded into the per-step state.

**Tests:**

- A unit test on the state-derivation function (pure, no React):
  the four turn states and the two tool states return from the
  function with the right values for the right inputs.
- Two grep guards in `test_web_tokens.py`: the Flow screen renders
  a state pill per turn and per tool, and the previously-implicit
  attempt pill is gone.

**Decisions:** the operator's call on 2026-09-08 that "trên UI đang
không hiển thị được cái nào là tool call, cái nào là loop lần 1, lần 2".

**Status:** ready-for-agent
