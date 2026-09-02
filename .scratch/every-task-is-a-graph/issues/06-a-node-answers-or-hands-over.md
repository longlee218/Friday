# 06: A node answers, or hands over

**What to build:** A node's agent reports what it concluded by calling a tool
— *this is the answer*, *this is why I am stopping* — instead of returning
prose that code then has to read. Approval for anything a reporter sees does
not move: it stays at the outbox, on the same card, exactly as today.

**Blocked by:** 04

**Decisions:** D13, D14

**Status:** ready-for-agent

## Why

Triage already reports its conclusion by calling a tool, and that is the shape
that needs no parsing. The nodes still return strings and sentinels — `CANNOT
FIX`, `NOT FOUND`, `NO LOGS` — and each one is a small private protocol
between a prompt and the function that reads it. When the model wanders off
the protocol the failure is silent: the sentinel is not matched, the prose
becomes the value, and it is proposed as a reply under the operator's name.
That has happened here, wearing a Markdown code fence.

`hand_over(reason)` replaces `Park`, and the reason is the node agent's own
finding quoted to the operator — not a message to anyone else. A graph that
walks its whole path without answering hands over by code, with no model
involved, because "it did not say" is not something to ask a model about.

**Not in this ticket:** the analysis node's JSON shape. No decision in this
spec covers it, and inventing one here would be building past what was agreed.

## Acceptance criteria

- [ ] The composing node's agent reports its answer by calling `answer(text)`;
      nothing parses its prose looking for one
- [ ] Any node may call `hand_over(reason)`; the reason reaches the operator
      quoted, and never reaches a reporter under the operator's name
- [ ] A graph that reaches its end without answering hands over by code
- [ ] Approval for a reply is untouched: the same outbox predicate, the same
      Discord card, restart-safe, provider-agnostic
- [ ] `Park` is gone as a name; hand-over is what the system calls it, in the
      code and on the board
- [ ] Driven at the scripted-model seam for the tool calls, and at the pool's
      `run_once` seam for what lands in the outbox
- [ ] Exempt from the byte-identity rule — D13 and D14 change behaviour
