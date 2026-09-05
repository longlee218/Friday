# 03: A call that costs too much does not happen

**What to build:** `max_tokens` per agent and a daily ceiling per agent. A
breach is work for a person, not a truncated answer.

**The per-run ceiling is dropped**, on the operator's call — "tính theo budget
1 ngày thôi" — and the criterion as written could not have been met anyway:
it said "enforced before the call is made", and the number of tokens a run
will cost is not known until it has cost them. The only pre-call figure is an
estimate of the prompt, which is a different quantity wearing the same name. A
daily total is measured rather than estimated, and it is the one an operator
actually has an opinion about.

**Blocked by:** 01, 02

**Decisions:** D5

**Status:** done

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

- [x] `max_tokens` settable per agent in `config.yaml`, passed through
      `ModelSettings`
- [x] A per-agent daily ceiling, configured, enforced in the middleware
      before the call is made
- [x] A breach produces a `HandOver` with a reason naming the ceiling, never a
      truncated or silent result
- [x] The daily total is read from `model_calls`, not held in memory — a
      restart must not reset a budget
- [x] The heartbeat line says what has been spent today
- [x] Each guard is deleted once and watched go red

## What it came to

**`max_tokens` needed no code.** `settings:` is splatted into `ModelSettings`,
so anything that class accepts has been configurable per agent all along. That
is now a test rather than a comment, because the alternative is somebody
adding a `max_tokens:` field beside it and creating two ways to say one thing.

**The budget is read, never counted.** `Database.spent_today(agent)` sums the
rows the agent wrote since midnight UTC — midnight UTC because a budget needs
a boundary that does not move and the process has no opinion about where the
operator is. Per agent because they are different jobs against different
models, and a shared pool would let the cheap high-volume one exhaust the
careful one.

**Off by default, and measured anyway.** `docs/DESIGN.md` says the first weeks
are data collection; a number guessed before anyone knows what a normal day
costs would make the first busy day look like a fault. So the heartbeat
reports the spend whether or not a ceiling exists, and an agent with no budget
is never asked what it spent — the query stays off the hot path for installs
that have not opted in.

**It fails open, and that is a decision.** The check runs before `Runner.run`
and so outside the clause that turns every other failure into a `last_error` —
left bare it was the one path in this module that could raise past every
caller, which is the rule the whole file is built on. Refusing on a failed
read would turn a store that cannot answer one question into every agent
refusing at once, and a store in that state has already stopped the work by
other means. Caught, logged, and the call goes ahead.

**A refusal is not a discard.** `tests/test_triage_runner.py` drives it end to
end: an agent over its ceiling produces no call, and the mention still lands
in `NEEDS_HUMAN` carrying the reason. The never-drop rule has no exception for
running out of money.

708 tests pass (702 before, +6). Five guards, each deleted once and watched go
red — including the one for the ledger error, which was written because the
first version of the check sat outside the try.

## What the review changed

Three findings, all mine.

**The composition root's wiring of the ceiling was unguarded.** The AST test
that exists precisely to catch a forgotten wire — its docstring says
"forgetting is the failure mode this guards, so the guard has to be
mechanical" — read one keyword while a second was being threaded through the
same four calls three lines away. Deleting every `spent=db.spent_today` left
the suite green. It loops over `SEAMS` now, and the next seam threaded through
those builders is one entry rather than a new test.

**The ceiling's boundary was untested.** One test sat well over it and one
well under, so `spent < budget` could have been `spent <= budget` — a whole
budget's worth of overspend — and stayed green.

**And the claim that a breach becomes work for a person was true of one caller
out of four.** The extractor was the harmful one: `Harness.run` returns `None`,
`last_error` was dropped on the floor, the fields came back empty, and the
code floor then asked the reporter for the correlationId they had written in
their first message. That is verbatim the failure `CLAUDE.md` names as the
reason a task type without an extractor is *broken* rather than degraded — a
ceiling reproduced it, and nothing said why.

`Harness.refusal` now says a run did not happen, as against happening and
failing, and `friday/extraction` raises `Refused` on it so node 0 hands over
with the reason. `Harness` still never raises: that rule is older than this
distinction, so it reports and a caller with somewhere better to send it does
the raising.

The other two are documented rather than changed, because they are not the
same failure: the responder falls back to the plain template and the message
still goes out, and the summariser skips a rebuild. `CLAUDE.md` now says which
of the four hand over instead of claiming all of them do.

712 tests pass (709 before, +3). Nine guards in this ticket, each deleted once
and watched go red.
