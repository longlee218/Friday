# 06: A node answers, or hands over

**What to build:** A node's agent reports what it concluded by calling a tool
— *this is the answer*, *this is why I am stopping* — instead of returning
prose that code then has to read. Approval for anything a reporter sees does
not move: it stays at the outbox, on the same card, exactly as today.

**Blocked by:** 04

**Decisions:** D13, D14

**Status:** done

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

- [x] The composing node's agent reports its answer by calling `answer(text)`;
      nothing parses its prose looking for one
- [x] Any node may call `hand_over(reason)`; the reason reaches the operator
      quoted, and never reaches a reporter under the operator's name
- [x] A graph that reaches its end without answering hands over by code
- [x] Approval for a reply is untouched: the same outbox predicate, the same
      Discord card, restart-safe, provider-agnostic
- [x] `Park` is gone as a name; hand-over is what the system calls it, in the
      code and on the board
- [x] Driven at the scripted-model seam for the tool calls, and at the pool's
      `run_once` seam for what lands in the outbox
- [x] Exempt from the byte-identity rule — D13 and D14 change behaviour

## What it came to

**`Park` → `HandOver`, everywhere.** The dataclass, its docstring explaining
the tool that replaced it, every import, every `isinstance` check, every
test name and docstring that said "park" — six source files, five test
files, both docs. A new hygiene test (`test_park_is_gone_as_a_name`) greps
all of `friday/` for the bare word, one line in `domain/actions.py`
excepted (the rename note itself) — mutation-tested by reinjecting the word
and watching it fail.

**`answer`/`hand_over` on `compose_reply`.** `ComposeCapture` (one field,
`action: Action | None`) plus two `@tool`-decorated functions live in
`api_issue.py`, exported as `COMPOSE_TOOLS`. `router.py`'s
`agents_for_api_issue` wires them onto `compose_reply`'s Harness alongside
`fetch_skill` (compose_reply is a `REASONING` node too), with
`tool_use_behavior={"stop_at_tool_names": [...]}` rather than
`stop_on_first_tool` — naming the two tools specifically, so fetching a
skill mid-answer does not end the run before the answer itself does. The
node reads `capture.action`; nothing reads `.final_output` any more. A model
that writes prose without calling either tool gets the same fallback a
missing agent gets (`Reply(said)`, the raw cause+fix) — never its own prose.

**A real bug found and fixed, not just renamed.** `fix_bug`'s prompt asked
the model to "refuse, by saying exactly CANNOT FIX and why" — and the node's
own code never checked for that sentinel anywhere. A model that refused
correctly had its refusal treated as the diff and proposed to the reporter.
Given `hand_over` already existed for `compose_reply`, wiring the same tool
onto `fix_bug` (reusing `ComposeCapture` — it is generic to "a tool wrote an
`Action` here", not specific to composing a reply) closed this for the case
where the model uses the tool as the rewritten prompt now asks. This is
within D14's "any node may call `hand_over`," not a separate decision.

**The graph's own fallback** (`_outcome`'s "finished without deciding what
to send") is unchanged mechanism, renamed: still code, no model asked, still
the honest answer for a graph that walked its whole path without an agent
producing an `Action`.

**Tests.** Scripted-model seam: `answer` becoming the `Reply`, `hand_over`
becoming the `HandOver`, prose-with-no-tool-call falling back correctly (all
three mutation-tested), and the same pattern for `fix_bug`'s `hand_over`.
Pool's `run_once` seam: unchanged — the existing hand-over/reply/outbox
tests (renamed, not rewritten) already cover approval and what lands where,
since D13/D14 didn't touch that mechanism.

622 tests, up from 617: one hygiene test, three for `compose_reply`'s tools,
one for `fix_bug`'s. Several more renamed (not rewritten) for the vocabulary
change. `friday/dag/prompt.py` is the only prompt file touched
(`compose_reply` and `fix_bug`'s instructions now describe the tools),
deliberately, per the exemption.
