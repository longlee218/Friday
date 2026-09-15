# 46: The operator answering first is the case that is missed

**What to build:** An operator answer written before the task row existed
closes the task, the same as one written after it.

**Blocked by:** None (can start immediately)

**Status:** done

## What is wrong

Ticket 37 built "the operator's own message ends the work", and it works
whenever the operator answers *after* the agent has opened a task. The
opposite case — the operator answering before the agent got there — silently
does not count.

`Database._operator_answered` (`friday/store/db.py:2082-2094`) asks for the
operator's messages in the conversation with:

```
schema.Message.created_at > task.created_at
```

Those two columns are not the same kind of time.

- `Message.created_at` is the Discord authored timestamp, copied straight
  through by `normalise.py:38`.
- `Task.created_at` is `_now()` at the moment the row is inserted
  (`db.py:1856`), which is when **triage** got to the message, not when
  anybody wrote anything.

Between the two sits the turn window. A mention opens a turn; the turn is not
read until the reporter has been quiet for `turn_seconds` (12) or somebody
else speaks; `TriageRunner` polls every 2 seconds. So the task row is stamped
roughly fourteen seconds after the reporter wrote, and anything the operator
typed inside that window sorts before the task it answers.

**The faster the operator is, the more certain the miss.** The operator
speaking is itself one of the two things that closes the reporter's turn
(`turn_from`), so answering quickly is exactly what pushes `task.created_at`
past their own message. Answer in ten seconds and it is lost; answer in five
minutes and it works.

What the reporter sees: the operator answers them, and fourteen seconds later
the agent asks them for a correlationId about the thing that was just
answered. That is the failure ticket 37 exists to prevent, arriving through
the door ticket 37 did not look at.

## Evidence

Probed against the task row at four offsets. The outcome flips exactly at
zero, which is `task.created_at` and nothing else:

```
secs=-9  -> needs_human
secs=-1  -> needs_human
secs=+1  -> handled_by_operator
secs=+10 -> handled_by_operator
```

The reproduction is in the suite as
`test_an_answer_written_before_the_task_row_existed_still_closes_it`
(`tests/test_pool.py`), passing.

**This paragraph said the test was marked `xfail(strict=True)`, and by the
time the ticket was implemented that was no longer true.** An xfail
reproduction was written first, and it turned out to describe the wrong
shape: it used `make_task` with no message linked to it, which is exactly the
case this fix deliberately leaves alone, so it would have stayed red after a
correct fix. It was replaced rather than unmarked, by four tests that link
the reporter's mention the way triage does. Recorded rather than quietly
edited, because a ticket that describes a test the suite does not contain is
the drift this repo keeps writing paragraphs about.

The replacement was checked against the same bar the strict marker existed to
enforce: with the one line reverted to `> task.created_at`, the two tests that
carry the bug go red, and with the boundary removed altogether the guard test
goes red.

## What to change

The comparison needs a time that means "when the work this task is about was
reported", not "when this process noticed". The messages table already
carries it: the task's own linked messages, whose `created_at` is Discord's
clock.

So: compare the operator's message against the **earliest `Message.created_at`
where `task_id` is this task**, rather than against `task.created_at`. Both
sides then read the same clock and the window disappears rather than being
narrowed. `Database.source_message_of` (`db.py:1906`) already selects exactly
that row, but returns its `provider_message_id` rather than its timestamp, so
this is either a second lookup or — better, and one round trip — a subquery
inside `_operator_answered`'s existing statement.

**Keep `task.created_at` as the fallback.** `source_message_of`'s own
docstring names the cases where a task has no linked message: a manually
seeded task, and a follow-up whose linkage was lost. Those must keep behaving
as they do now rather than comparing against nothing.

**Do not fix it by widening the window** (subtracting `turn_seconds`, or a
grace period). That trades a certain miss for an uncertain one and leaves two
numbers that have to be kept in agreement with a third, which is the drift
shape this repo has already been bitten by more than once.

**Watch the sibling case while you are here.** A backfilled task — the sweep
recovering a mention after downtime — has the same shape at a much larger
scale: the task is created now, every message in it was written hours ago, so
*no* operator answer can ever count. Board `work-that-has-gone-cold` ticket 02
names this as a thing it must not have to solve separately; fixing the
comparison here fixes it there too, and that is the reason to fix it here
rather than in the sweep.

## Acceptance

- A test carrying the bug passes on its own, and goes red with the one-line
  change reverted. (This read "the xfail marker is deleted" — see Evidence.)
- `test_the_operator_answering_closes_the_task_and_withdraws_the_draft` and
  `test_a_message_this_process_posted_is_not_the_operator_answering` still
  pass — the after-the-task case and the not-us case are unchanged.
- A task whose messages were all written before the task row existed (the
  backfill shape) closes when the operator answered in that history. Bounded
  in practice by `max_message_age`: a turn older than the cutoff never opens a
  task, so the backfill shape that can reach here is the one inside it, which
  is also exactly what board `work-that-has-gone-cold` ticket 02's chosen
  option B recovers.
- `uv run pytest -q` green.
