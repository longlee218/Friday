# 01: A message can be too old to answer

**What to build:** A message whose author wrote it more than
`max_message_age_hours` ago is marked outdated and never reaches the model.

**Blocked by:** None

**Decisions:** D1–D4, below

**Status:** todo

## Why

Nothing in this system looks at how old a message is. `friday/inbox/` reads
the age of a *typing* signal and nothing else; triage classifies whatever is
in the queue whenever it gets to it. So the queue has no notion of a report
that has stopped being worth answering.

Two ways that bites, and the second is the one that actually happens.

**A backlog after downtime.** `sweep_once` asks each channel for everything
after `cursor_for(provider, channel_id)`, and a channel with no cursor gets
`after=None` — the whole history the provider will hand over. That is exactly
the state after a restart on a wiped database, which happened on 2026-09-07.
The agent then classifies, opens tasks for, and asks questions about a week of
messages whose authors have long since moved on. The cursor design is right —
re-reading is free because `record_message` deduplicates — but "free" is about
storage, not about the reporter's inbox.

**A slow queue.** Triage runs off a poll, one message at a time, behind a
turn window. A burst, a provider outage, or a budget refusal can leave a
mention sitting for hours. Answering "which environment?" about something
somebody reported yesterday and fixed themselves is worse than not answering.

## The invariant this has to be argued against

`CLAUDE.md`: **"Never drop a mention.** Low confidence, turn-cap and token-cap
breaches, refusals, classifier errors and the sensitive-word prefilter all
route to `HITL` — never to a silent discard. A dropped mention is
indistinguishable from correct operation."

This ticket adds a rule that stops work happening, so it has to say why it is
not that. The distinction it turns on: **the mention is kept, recorded, and
visible; what is skipped is the model call and the reply.** Nothing is
deleted, nothing is unaccounted for, and the board can show exactly how many
messages went this way and which ones. What the invariant forbids is a
mention that leaves no trace — not a mention somebody decided not to answer.

The closest existing precedent is the sensitive-word prefilter, and the
difference is worth being precise about rather than glossing. That one
**holds** a message for a person, because the operator still needs to see it.
This one does not: the whole point is that nobody needs to act. So it is
genuinely a new outcome, not an existing one reused.

## Decisions

- **D1. The cutoff is applied at triage, not at ingest.** `Inbox._accept` is
  where a message could be refused most cheaply, and that is the wrong place:
  a message not stored has no row, and "a dropped mention is
  indistinguishable from correct operation" is precisely about rows that do
  not exist. Store it, mark it, skip the model. The cost is one row per stale
  message, which is what the message table is for.

- **D2. Age is measured from `created_at` — when the person wrote it — not
  from when it was captured.** A message the sweep finds a week late is a week
  old, and that is the fact that matters to the person who wrote it. Measuring
  from capture time would make every backfilled message look fresh, which
  inverts the case this exists for.

- **D3. `outdated` is a triage decision, not a `TaskState`.** It records
  through `mark_triaged` with no task opened — the same shape `skip` already
  has, and for the same reason: everything `classify` names opens work, and
  this names the absence of it. `TaskState` stays six states with its existing
  transitions; nothing new can be reached from `PENDING`.

  It is **not** added to `CLASSIFIABLE`. That tuple decides what may become a
  few-shot example, and "this was old" is not something the classifier should
  learn to predict from a message's text — it is a fact about the clock.

- **D4. The threshold is configuration, and the default is off.** A number
  guessed before anyone knows what a normal delay looks like will discard real
  work on its first busy day — the argument `config.yaml` already makes for
  `daily_token_budget` being unset. `max_message_age_hours` absent means no
  cutoff and the behaviour that exists today; the operator sets it once they
  have seen the board's number.

## Acceptance criteria

- [ ] A message older than the configured age is recorded with an `outdated`
      decision, opens no task, and reaches no model — asserted by a test that
      fails if a model call happens
- [ ] With no `max_message_age_hours` configured, behaviour is exactly what it
      is today, and a test says so
- [ ] Age is computed from `created_at`; a test covers the backfill case — a
      message captured now that was written three days ago
- [ ] The boundary is tested at exactly the cutoff, not only well past it
- [ ] The message is still stored and still visible on the board, with the
      reason legible — this is the whole of the never-drop argument, so it is a
      test and not a note
- [ ] A `/flow` path for an outdated message renders as a complete outcome,
      the way `skip` and the prefilter hold already do
- [ ] The startup log says whether a cutoff is in force, the way the
      sensitive-word list already does
- [ ] `CLAUDE.md`'s never-drop paragraph gains this outcome and the sentence
      distinguishing it from the prefilter's hold
- [ ] Each guard deleted once and watched go red

## Open — needs the operator

Three things this ticket does not decide, because they are judgement calls
about how the system should behave rather than about how to build it.

1. **Does an outdated message still count as a turn for the messages around
   it?** A turn is everything one person went on to say. If somebody wrote
   three messages 25 hours ago and a fourth an hour ago, is that one turn with
   an answer owed, or three stale messages and one fresh one? Recommendation:
   the *turn* is judged by its newest message, so a live follow-up pulls the
   older ones back in with it. Otherwise a reporter who returns to their own
   thread is answered without the context they wrote.

2. **What about a reply to something the agent asked?** `task_answered_by`
   routes a reply to the task it answers, deliberately bypassing
   classification. If the reporter answers a question three days later, the
   task is still open and still waiting. Recommendation: exempt it — a task
   in `WAITING_FOR_DETAILS` asked for something, and getting it late is still
   getting it.

3. **Is 24 hours right?** The number in the request. It is a fine starting
   value and this ticket makes it configurable rather than fixed, so the
   question is only what to ship as the documented suggestion.
