# Spec: nothing runs unmeasured

Status: ready-for-agent. Came out of a design review on 2026-09-05 rather than
from planning — the review read the code, not the docs, and four of the eight
findings are things `CLAUDE.md` currently describes as working.

## Problem Statement

Every model call in this system goes through one function — `Harness._settle`
— and that function does four things: it runs the agent, it swallows the
exception, it writes a `last_error`, and it attaches a logging hook. It does
not time the call out, it does not know what the call cost, it cannot tell a
rate limit from a bad prompt, and whether the call is recorded at all depends
on the caller remembering to pass a `calls=` list.

Three of the four callers forget. `friday/triage/runner.py:215` passes it;
`friday/extraction/__init__.py:79`, `friday/memory/channel_context.py:280`
and — through `Pool._in_the_operators_voice` — `friday/responder/__init__.py:181`
do not. So the `model_calls` table holds triage and nothing else, while the
board and `CLAUDE.md` both say it holds every model prompt. The two steps that
produce text a person reads are the two with no record of what they were sent.

That is the observability half. Three more, from the same read:

**Nothing is capped.** `input_tokens` is stored for triage and never summed.
No price, no per-task ceiling, no per-day ceiling, no `max_tokens` in
`ModelSettings`. `docs/DESIGN.md`'s accepted risk 4 names the threshold and the
node caps as the levers against cost, and neither is measurable today.

**A hiccup is permanent.** A 429 or a 502 becomes `last_error`, becomes a
`HandOver`, becomes `NEEDS_HUMAN`, becomes a DM. There is no retry at the model
layer at all — while the outbox, one layer further out and one order of
magnitude cheaper, has `retry_after`, doubling backoff and `max_attempts`.

**Nothing is scored.** `docs/DESIGN.md` promises triage a fixture set of real
messages with expected labels, "the regression net for prompt changes". It does
not exist. The tests drive a scripted transport, so they pin wiring, not
classification. Meanwhile `confidence_threshold: 0.7` is documented in
`config.yaml` as a number not to trust yet, and the operator's own ✅/❌ marks —
a labelled set, already in the database — are used to *teach* the classifier
and never to *score* it.

And one that is not about measurement at all, found on the way past:
`auto_ask_for_details` is the single path allowed out with no approval, and the
text on it is written by a model whose input contains other people's channel
messages. `config.yaml` justifies the exemption with "what is being asked never
changes, only the wording does". Nothing enforces that sentence.

## Solution

One middleware around `Harness._settle`, since it is already the only door.
It times the call out, checks a budget before spending it, retries what is
worth retrying, and records what happened — always, from a sink handed in at
construction, never from an argument a caller can forget. `model_calls` grows
the keys that make it answerable per task and per node, and the graph's trail
is saved beside the checkpoint so "which way did this run go" survives a
restart.

The unreviewed ask gets a predicate before it is queued: still asks for the
missing fields, no links, no code, under a length, no promises. Fails it, the
template goes out instead.

And the classifier gets a frozen set built from marks the operator already
made, scored by a runner that talks to the real provider and is not part of
`pytest`.

## Implementation Decisions

- **D1. The recording sink is injected, not passed.** The `calls=` parameter is
  the bug, not the mechanism: three of four call sites forget it. The harness is
  handed a sink when it is built, by the composition root, and every call it
  makes is recorded whether or not anyone asked.
- **D2. The middleware wraps, it does not observe.** `AgentHooks` is a callback
  after the fact and cannot refuse a call. Budget and timeout have to sit
  around `Runner.run`, in `_settle`.
- **D3. A `ModelCall` names the work it belonged to.** `message_id` alone cannot
  answer "what did task 42 cost". Add `task_id`, `node`, `latency_ms`,
  `attempts`.
- **D4. Transient and terminal are different outcomes.** A retry budget at the
  model layer, then a `HandOver` — the same shape the outbox already uses, for
  the same reason. What counts as transient is a small explicit list, not a
  guess from the message text.
- **D5. Caps live in `config.yaml` per agent**, like every other knob: a
  `max_tokens` in `settings`, a per-run token ceiling, and a daily ceiling per
  agent. A breach is work for a person, never a silent truncation — the same
  rule as every other refusal in this system.
- **D6. The eval runner is not a test.** It costs money and needs the network.
  It lives in `evals/`, is run by hand, and prints accuracy, a confusion
  matrix, and what the threshold would have done at each value.
- **D7. The frozen set is a file, not a query.** Built once from
  `confirmed_classifications`, then committed. A set that re-reads the database
  changes under the prompt it is scoring.
- **D8. Code is the floor for the unreviewed ask**, the same rule
  `friday/dag/prepare.py` already states for validation: a model's wording is
  accepted only when a predicate agrees it still asks the same question.
