# 04: A hiccup is not a person's problem

**What to build:** The model layer retries what is worth retrying, with backoff,
and hands over only what is terminal — the shape the outbox already has.

**Blocked by:** 01

**Decisions:** D4

**Status:** done

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

- [x] `AsyncOpenAI` is constructed with a chosen `timeout` and `max_retries`,
      not the SDK's defaults, and the chosen values are in `config.yaml`
- [x] A transient failure is retried in the middleware with doubling backoff up
      to a configured attempt count, then hands over
- [x] A terminal failure hands over on the first attempt
- [x] Every attempt is recorded, so the record and the invoice agree
- [x] The hand-over reason says which of the two it was
- [x] Each guard is deleted once and watched go red

## What it came to

**The retry is ours, and the client's is off.** `max_retries=0` on
`AsyncOpenAI`, a loop in `Harness._attempts`. The operator's call, and the
ticket's own criterion forces it: the client retries silently, so the provider
bills three calls where the record holds one, and "the record and the invoice
agree" is not something a silent retry can be talked into. The cost is a
dumber retry — doubling, and no reading of a `Retry-After` header.

**`timeout_seconds` bounds the whole run, retries inside it**, also the
operator's call. It keeps the guarantee ticket 01 wrote into CLAUDE.md: the
pool works one task at a time, so three tries at sixty seconds each would be
three minutes of *every* task waiting. A hiccup late in the budget then has
little room to try again, which is the right way round — that run was already
slow.

**`attempt` is an ordinal, not a total**, and the ticket called it `attempts`
before the shape was known. One row is one call to the provider; a run
rate-limited once leaves two rows, each with its own prompt and its own cost,
rather than one row claiming to be two. The failed attempt's row carries what
it sent and an empty answer, because that is what it got.

**Numbering them needed a value passed down.** The first version smuggled the
attempt number onto the exception (`exc._friday_attempts`) and worked out
which rows were new by counting the ones already numbered — which was wrong,
and quietly: the second attempt's row came back as attempt 1. `_Progress`
carries the attempt in flight and how much of the record it has claimed, and
both readers get it — the loop as it goes, and the `finally` for whatever a
cancelled attempt left behind.

**A test builds provider errors by hand.** The SDK vendors its HTTP library
under a private name, so `import httpx` fails; reaching for `httpx2` instead
would make these tests break on an upgrade for a reason having nothing to do
with what they check.

717 tests pass (712 before, +5). Five guards, each deleted once and watched go
red — the last of them written because the first four left `max_retries=0`
unpinned, and that is the line the whole "record agrees with invoice"
argument rests on.

## What the review changed

Three findings, all mine, and the first is the one this ticket exists to
prevent — in the opposite direction.

**Every failing path recorded its last attempt twice.** `LogHooks.unfinished()`
built a row from `_pending` and never cleared it, and its docstring said that
was safe because `on_llm_end` clears it — true when there was one caller, and
false the moment the retry loop began flushing per attempt *and* the harness
kept flushing in its `finally`. Measured: three 429s recorded `[1, 2, 3, 3]`;
a terminal 400 recorded two rows. So a run that made three calls recorded
four, and the record over-counting the invoice is the same failure as
under-counting it. `unfinished()` consumes now.

Six green guards missed it for one reason: the three failure tests passed **no
`record=` sink at all**, so nothing observed the thing they were about. The
success-after-retry test did, which is why that path was the only correct one.

**408 was treated as the provider's final answer.** The `status_code >= 500`
branch could never fire — the client maps every status at or above 500 to
`InternalServerError`, already on the list — while 408 Request Timeout arrived
as a bare `APIStatusError` and fell through it. It is the one status that
means "did not arrive in time", which is the definition of worth asking again.
An explicit set of statuses replaces a comparison that was dead code with a
false comment.

**`APITimeoutError` was on the retry list and could not fire.** The client and
the run were given the same number, so the run-level timer always tripped
first — and it cancels, which is a `BaseException` the retry loop never sees.
A hung provider therefore burned the whole budget on one attempt and reported
"no answer within 60s", never "gave up after 3 attempts". One request now gets
`timeout_seconds / max_attempts`. That is not the tradeoff this ticket already
acknowledged: that one is about a transient *error* late in the budget, and a
hang had no room at any point in it.

720 tests pass (718 before, +2). Eight guards in this ticket, each deleted
once and watched go red.
