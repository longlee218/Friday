# 03: A call that costs too much does not happen

**What to build:** `max_tokens` per agent, a per-run token ceiling, and a daily
ceiling per agent. A breach is work for a person, not a truncated answer.

**Blocked by:** 01, 02

**Decisions:** D5

**Status:** todo

## Why

`docs/DESIGN.md`'s accepted risk 4 says cost scales with mention volume and
names "the threshold and node caps" as the levers. `max_turns` is the only cap
that exists, and it is the wrong unit — one turn is 200 tokens or 200,000
depending on what the reporter pasted. `ModelSettings(**config.settings)`
accepts `max_tokens` and no agent sets one.

That this already bites is visible in the responder: `_UNCLOSED`
(`friday/responder/__init__.py:~205`) exists because a truncated response
leaves `<think>` open. Truncation is being handled downstream instead of being
bounded upstream.

A ceiling must hand over rather than trim. Every other refusal in this system
routes to a person — low confidence, turn caps, the sensitive-word prefilter —
and a silently shortened answer under the operator's name is exactly the
outcome those rules exist to prevent.

## Acceptance criteria

- [ ] `max_tokens` settable per agent in `config.yaml`, passed through
      `ModelSettings`
- [ ] A per-run token ceiling and a per-agent daily ceiling, both configured,
      both enforced in the middleware before the call is made
- [ ] A breach produces a `HandOver` with a reason naming the ceiling, never a
      truncated or silent result
- [ ] The daily total is read from `model_calls`, not held in memory — a
      restart must not reset a budget
- [ ] The heartbeat line says what has been spent today
- [ ] Each guard is deleted once and watched go red
