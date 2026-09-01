# 37: The operator's own message ends the work

**What to build:** When the operator answers somebody themselves, the agent
stops. The task closes, and anything queued about it — including a draft
already written and waiting for approval — never goes out.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

## Why

Today nothing happens. Their message is dropped from scope for being written by
the watched account, the task stays where it was, and the agent goes on asking
about a question that has been answered. A draft waits for approval with no
expiry, so approving it two days later sends an answer that stopped being true
the moment they typed.

## Two questions that are currently one

`is_own` decides both "does this create work" and "does this get looked at",
and they need different answers:

- **It never creates work.** Unchanged — that rule exists because the agent
  would otherwise answer its own replies, which it did, nineteen times.
- **It is always observed**, because it is what ends work.

Telling the operator's own typing apart from what this process posted is
already solved and must be reused, not rebuilt.

`capture_own_messages` loses its reason to exist once the two are separate: it
was a testing-only switch for making the first question answerable, and after
this the answer is always yes to one half and always no to the other.

## Which task closes

- They replied to something we sent → the task that message was about. The
  lookup added in ticket 34 answers this for their replies as much as anyone
  else's.
- They typed without replying, and the conversation has exactly one open task →
  that one.
- Several open tasks and no reply → close nothing. Guessing here loses work,
  and when it is not clear the answer is a person, not a guess.

## Not `done`

A new state. `done` is terminal and means the agent finished; this means a
person did it instead, and it has to be reopenable because closing on "they
said something in this channel" will sometimes be wrong.

It is also the only number that says whether this system is helping: what share
of the work still lands on the operator. Folding it into `done` loses that for
good.

## Acceptance criteria

- [ ] The operator answering by hand closes the task the answer belongs to
- [ ] Queued outbound rows for that task never send, including a `reply` that
      was drafted and is waiting for approval
- [ ] Nothing is sent to say a draft was cancelled — it is a message about
      something that correctly did not happen
- [ ] The operator's messages still create no work of their own
- [ ] A message this process posted does not count as the operator answering
- [ ] The new state is distinguishable from `done` on the board and can be
      reopened
- [ ] With several open tasks in one conversation and no reply, none close
- [ ] `capture_own_messages` is gone, and the tests that relied on it now
      exercise the split rule instead
