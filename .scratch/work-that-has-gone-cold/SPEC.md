# Spec: work that has gone cold

Status: ticket 01 written 2026-09-07, from the operator, and **done** —
`max_message_age` was left unset until 2026-09-15, when it was set to `'24h'`
in `config.yaml`, so the rule this board describes is only actually running
from that date. Ticket 02 was written the same day, after
investigating the cold-cursor sweep for something else turned up that the
paragraph below describes it wrongly. The operator chose option B — a cold
cursor looks back exactly as far as `max_message_age` — so 02 is
`ready-for-agent`, blocked on ticket 46 of `discord-mention-triage`, which is
a defect B would otherwise walk straight into. Opened as its own
board rather than added to an existing one because neither covers the
question: `nothing-runs-unmeasured` was about what a run costs and whether it
is recorded, and `a-window-on-the-whole-path` about seeing it afterwards.
This one is about work the system should decline to start.

## Problem Statement

Nothing here knows how old anything is. The only age this system measures is
a typing signal's, inside the turn window. Triage classifies whatever is in
the queue whenever it reaches it, and the queue is FIFO with no expiry, so
"this message has been waiting eleven hours" and "this message arrived a
second ago" are the same input.

That was fine while the queue was short and the cursor was warm. Two things
make it not fine:

**The sweep has no floor.** `Inbox.sweep_once` asks each channel for
everything after its cursor, and a channel with no cursor asks for the
beginning of the channel. That is the state after any restart against a fresh
database — including the one on 2026-09-07 — and the result is the agent
working through a backlog of messages whose authors moved on days ago,
opening tasks and asking them questions.

This paragraph said "asks for everything the provider will give" until
2026-09-15, and ticket 02 is what that wording was hiding: the sweep reads
the **oldest** hundred messages in the channel, then the next hundred five
minutes later, crawling forward from the day the channel was created. The
hundred is the library's default page size and no line of this repo chose
it. The rule below still holds — every one of those messages is now marked
`outdated` — but "which end does a cold cursor read" is a separate question
and has its own ticket.

**Nothing else can stop it.** Every existing guard is about *what* a message
is, not *when*: the sensitive-word prefilter reads its text, the confidence
threshold reads the classifier's certainty, the budget reads the day's spend.
None of them can express "too late to be worth answering", so the operator's
only lever today is not running the agent.

## Solution

One rule, applied before the model: a message whose author wrote it more than
`max_message_age_hours` ago is recorded as `outdated`, opens no task, and is
never sent anywhere. The message is kept and stays visible, which is what
separates this from the discard `CLAUDE.md` forbids.

Off by default, for the reason the token budget is: a number chosen before
anybody has seen what a normal delay looks like discards real work on its
first busy day.

## Implementation Decisions

D1–D4 are argued in ticket 01 rather than restated here, since there is one
ticket and the argument belongs where somebody implementing it will read it.
In short: the cutoff applies at triage rather than ingest (a mention with no
row is the thing the never-drop rule is actually about); age is measured from
when the message was written, not when it was captured; `outdated` is a
triage decision rather than a seventh `TaskState`; and the threshold is
configuration with no default.

## Out of scope

- **Anything that expires a task already open.** A task in
  `WAITING_FOR_DETAILS` for a week is a real question, and closing it on a
  timer is a different decision with a different failure mode — the reporter
  answers and finds the thread closed. If that is wanted it is its own
  ticket.
- **Anything that expires a queued outbound message.** The outbox retries
  over minutes and hands failures to a person; a message that is stale by the
  time it sends is a symptom of that path, not of this one.
- **Rate limiting or a queue depth cap.** Related in spirit — both are about
  refusing work — and neither is what was asked for.
