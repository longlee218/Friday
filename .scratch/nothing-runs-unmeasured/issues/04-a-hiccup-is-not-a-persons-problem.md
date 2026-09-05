# 04: A hiccup is not a person's problem

**What to build:** The model layer retries what is worth retrying, with backoff,
and hands over only what is terminal — the shape the outbox already has.

**Blocked by:** 01

**Decisions:** D4

**Status:** todo

## Why

Every failure of a model call is currently permanent. `_settle` catches
everything into `last_error` and returns `None`; `Pool._plan` turns that into a
`HandOver`; the task moves to `NEEDS_HUMAN`; `_raise_hands` DMs the operator. A
429 during a burst, a 502 from the provider, a dropped connection — each one
converts a task into human work that will never retry itself.

The asymmetry is the argument. `friday/outbox/__init__.py:132` has
`_retry_or_give_up`: `retry_after`, `backoff * 2**attempts`, `max_attempts`,
then a person. That is the cheap layer. The expensive layer, one call in, has
nothing.

Two things the SDK hides and this must not: it retries internally by default,
so the provider bills three calls where `LogHooks` records one; and
`AsyncOpenAI` is constructed in `_chat_model` with no `timeout` and no
`max_retries`, so its defaults are in force and nothing here has chosen them.

What counts as transient is an explicit list — connection errors, timeouts,
429, 5xx — not a guess from the exception text. A prompt the provider rejects
with a 400 is terminal and must not be retried at cost.

## Acceptance criteria

- [ ] `AsyncOpenAI` is constructed with a chosen `timeout` and `max_retries`,
      not the SDK's defaults, and the chosen values are in `config.yaml`
- [ ] A transient failure is retried in the middleware with doubling backoff up
      to a configured attempt count, then hands over
- [ ] A terminal failure hands over on the first attempt
- [ ] Every attempt is recorded, so the record and the invoice agree
- [ ] The hand-over reason says which of the two it was
- [ ] Each guard is deleted once and watched go red
