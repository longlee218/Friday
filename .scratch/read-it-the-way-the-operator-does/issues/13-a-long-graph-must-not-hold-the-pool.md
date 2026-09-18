# 13: A long graph must not hold the pool

**What to build:** Bounded concurrency in `Pool.run_once`, configured in
`config.yaml`, default small.

**Blocked by:** nothing. **Decisions:** finding B.
**Status:** ready-for-agent

## Why
`run_once` awaits each task in turn. Every graph so far finishes in seconds;
`api_issue` is bounded at five minutes, and for that long nothing else is
asked, drafted or handed over.

## Verify
- A slow graph and a one-node graph in one batch: the second finishes first.
- One task is never acted on twice concurrently.
