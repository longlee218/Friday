# 02: A cold cursor reads the wrong end of the channel

**What to build:** A sweep against a channel with no cursor starts from the
newest end, not the oldest, and says in code how far back it is willing to
look.

**Blocked by:** nothing — 46 (on board `discord-mention-triage`) is done, so
what "What B needs that is not in the sweep" describes below is already
fixed. Read that section anyway: it is why B is safe now and was not before.

**Decisions:** D8–D11, below. Continuing ticket 01's numbering, since these
are decisions about the same board's one rule and restarting at D1 would make
two D1s on one board.

**Status:** done, with one question left open — see "Still open"

## Why

Ticket 01 described the cold-cursor sweep as asking for "the whole history the
provider will hand over". That is not what it does, and the truth is worse in
one specific way: it reads the **oldest** end of the channel and crawls
forward, a hundred messages at a time, for as long as it takes.

The chain, all of it readable and none of it written down anywhere:

1. `Inbox.sweep_once` (`friday/inbox/__init__.py:129-131`) asks
   `cursor_for(...)`, which returns `None` on a fresh database.
2. `DiscordUser.history` (`friday/providers/discord/user.py:165-178`) passes
   that through as `after=None` and sets `oldest_first=True`.
3. `_replay` (`user.py:201-215`) calls `channel.history(**filters)` and
   **passes no `limit`**.
4. The library's default is `limit=100`
   (`discord_self/_vendor/discord/abc.py:2500`).

So `after=None` + `oldest_first=True` + an unstated `limit=100` is: the
hundred oldest messages in the channel, counted from the day it was created.
Not the hundred most recent.

It does not stop there. `_accept` advances the cursor after every message
(`inbox/__init__.py:178`), so the next sweep starts a hundred messages later.
The sweep runs on `sweep_interval_seconds`, which is 300. A channel with ten
thousand messages of history therefore takes about eight hours to crawl from
its own beginning to the present — and it starts crawling immediately at boot,
because `_handle_ready` sets `reconnected` on the **first** ready and not only
on a reconnect (`user.py:82-86`), which is the behaviour the sweep loop was
built to treat as "an outage just ended".

During those hours the recovery sweep is not recovering anything. The live
gateway is still catching new messages, so nothing is lost — but the safety
net that exists for the gap the gateway missed is, for that whole window,
pointed at the wrong decade.

## Why it is not urgent

`max_message_age` was enabled on 2026-09-15 (`config.yaml`, `agents.triage`,
`'24h'`). Ticket 01's guard sits before `_decide` in
`friday/triage/runner.py:252-259`, so every message this crawl turns up is
recorded as `outdated` and never reaches a model. The expensive half of the
failure — and the half that could not be taken back, an unapproved
`auto_ask_for_details` reply landing in a months-old thread under the
operator's name — is closed.

What is left is a sweep that spends hours being useless after a cold start,
and a hundred-message page size that no line of this repo's code has chosen.

## Why no test caught it

Both fakes make the library's default invisible. `tests/conftest.py:63-67`
yields every message it holds and never looks at a limit; the fake channel in
`tests/test_discord_provider.py:116` reads `kwargs.get("limit") or len(...)`,
so `limit=None` means "all of them" — the exact case that in production means
"a hundred". Whatever is decided below, the fakes have to model a page size,
or the suite stays unable to see this class of bug.

## The decision

**B, chosen by the operator on 2026-09-15.** The alternatives and why they
lost are kept below, because a decision with its rejected options deleted is
one the next reader has to re-derive.

- **D8. A cold cursor looks back exactly as far as `max_message_age`.** Read
  newest-first and stop at the first message older than the triage cutoff.
  The sweep's floor and the classifier's floor become one number with one
  meaning. A redeploy recovers everything that was still worth answering,
  which is the definition ticket 01 already wrote down.

  *Rejected: start at now (call it A).* Read the newest message, write it as
  the cursor, triage nothing before it. Simplest, and the boot sweep becomes
  instant. But cursor loss always comes with downtime, and during downtime
  the gateway is not running either — so the messages in that window reached
  nothing, and the sweep is their only recovery path. A discards them, and
  discards them silently, at the exact moment the system cannot know how long
  it was away. That is the shape `CLAUDE.md`'s never-drop rule names: a
  dropped mention is indistinguishable from correct operation.

  *Rejected: paginate properly and keep reading oldest-first (call it C).*
  Pass `limit=None` and let the library page through the whole history.
  Honest to never-drop in its strongest reading, but with ticket 01's cutoff
  running it does provably useless work: it spends REST calls and rows
  fetching messages the system has already decided it will not answer. On a
  long-lived channel the first run ingests everything, plus twenty
  `_seed_context` messages for every conversation it opens on the way.

- **D9. `_replay` passes `limit` explicitly.** Whatever the lookback, the page
  size stops being inherited from a library this repo has already written
  down as temporary. The hundred-message default was never chosen by any line
  of code here, and the only reason it was invisible is that both test fakes
  treat "no limit" as "all of them".

- **D10. The lookback reaches the inbox as a duration, not as a knob.** The
  wiring is under "How B is wired" below. `friday/inbox/` never learns that
  triage exists; a second number under `ingest:` is refused for the reason
  given there.

- **D11. A cold cursor is logged.** B's residual failure is downtime longer
  than the lookback, and that is acceptable only because it is visible. The
  requirement is under "How B is wired" below.

## How B is wired

`friday/inbox/` must not read a triage knob, and the inbox must receive a
number of seconds without ever learning that triage exists.

**How this section said to do that was wrong, and it was wrong when it was
written.** It said: "the composition root reads `max_message_age` and hands
`Inbox` a `cold_start_lookback` duration", on the strength of
`test_composition_root_reads_no_agent_config` existing. That guard does not
permit the composition root to read agent configuration — it *forbids* it,
which is the opposite of what this paragraph assumed, and it forbids it by
walking `run_agent.py`'s AST for any attribute chain containing `agents`. The
proposed wiring spells as `config.agents[...]` in `run_agent.py` and trips it
on the first run. Written without checking; recorded rather than edited away,
because the next reader deserves to know the instruction on this page was once
the opposite of the rule it cited.

What was built instead: `friday/config.py` grows `message_age_cutoff(config)`,
the one place that knows where that number lives, and both readers call it —
`TriageRunner.build` and a new `Inbox.build` classmethod, the same shape
`TriageRunner.build` and `ContextStore.build` already use. The composition
root reads nothing; the inbox imports one duration function and never names
triage. D10's intent is met; only its mechanism changed.

The alternative — a separate number under `ingest:` — is two values that have
to be kept in agreement, which is the drift shape `CLAUDE.md` devotes a
paragraph to and this repo has paid for more than once.

**A cold cursor must say so in the log.** B's residual failure is downtime
longer than the lookback: the gap beyond 24h is dropped, exactly as option A
drops everything. That is acceptable only if it is not silent. One line at
boot — which channel, that the cursor was cold, how far back it looked — is
the difference between a bounded decision and the never-drop rule being
quietly broken.

## What B needs that is not in the sweep

B deliberately pulls back messages that **will** be processed — that is the
point of it. So it wakes a defect that the `outdated` cutoff is currently
hiding.

`Database._operator_answered` (`friday/store/db.py:2082-2094`) asks whether
the operator said anything `created_at > task.created_at`. On a backfill the
task row is created *now* and every message in it was written hours ago, so
that comparison is false for all of them: **no operator answer in recovered
history can ever close a task.** The agent re-asks a reporter a question the
operator already answered by hand during the downtime — the failure ticket 37
was written to prevent.

This is not the sweep's bug to fix, and it is not only the sweep's bug:
probing showed it is reachable on the live path too, inside the ~14 second
window between a mention and its task row. It is now
`.scratch/discord-mention-triage/issues/46-the-operator-answering-first-is-missed.md`,
with a strict-xfail reproduction in `tests/test_pool.py`. Fixing 46 fixes
this, which is why this ticket lists it as a blocker rather than restating it.

## Still open

**What a cold cursor should do when `max_message_age` is unset.** This ticket
defines the lookback as that number, so with the number absent there is
nothing to look back by — and both ways of resolving it are options this
ticket already rejected: reading the whole channel is C, reading nothing is A.
Neither was chosen for this sub-case, so the implementation changed nothing
about it: that path reads as it always did, a forward page at a time, with the
page size now *stated* (`_PAGE` in `friday/providers/discord/user.py`) rather
than inherited from the library, which is all D9 asked for.

The original bug therefore survives in that one configuration, and says so in
a comment where it lives. Worth a ticket if anyone runs without a cutoff; it
is not this one's to decide twice.

## Known residuals

Both found by review rather than by the ticket, and both accepted rather than
fixed here.

- **A turn straddling the lookback is truncated, not skipped.** `_since` cuts
  per message; `TriageRunner._age` judges a turn by its **newest** message
  (D5 of ticket 01). So a burst that began just before the cutoff and
  continued past it is classified as fresh, from a transcript whose older half
  the sweep never fetched. Narrow — a turn is a run of messages under
  `turn_seconds` apart, so this needs a burst in flight across that exact
  instant — and the alternative is over-reading by an amount nothing bounds.

- **The two floors are one number, not one rule.** D8's phrase "one number
  with one meaning" is true of the number and slightly generous about the
  meaning: the sweep's floor cuts messages, the classifier's judges turns.
  That difference is the residual above and is the whole of it.

## Out of scope

- **`context_messages`.** The twenty messages `_seed_context` pulls per new
  conversation (`inbox/__init__.py:244-262`) are deliberate, documented at
  `config.yaml:282`, and not part of this.
- **`sweep_interval_seconds`.** Making the crawl faster is not the same as
  making it start in the right place, and tuning it would hide this rather
  than fix it.
- **The first-ready-sets-reconnected behaviour.** Sweeping at boot is
  correct; where it sweeps from is what this ticket is about.
