# 35: Tell the operator what they were asked

**What to build:** When a task lands on the operator, the message that tells
them carries what the reporter last said. If somebody asked a question the
agent could not answer, the operator can answer it from the notification —
without opening the board to find out why the task is sitting there.

**Blocked by:** 34 (a reply belongs to the task it answers)

**Status:** ready-for-agent

## Why

The announcement today is the task's type, its id, and its parameters:

    api_issue #1 — correlation_id: abcdef01-…, environment: production

Everything in that line is true and none of it is the reason the task stopped.
The reporter asked what a correlationId is; the agent has no way to answer,
which is correct, and no way to say that either, which is not. The operator
sees a task with a correlationId in it and no hint that a person is waiting on
a sentence they could write in five seconds.

This is the escalation tier of the explaining path: the agent explains what it
can from what the operator has written down, and hands over what it cannot.
Handing over without the question is handing over the wrong half.

It is also how the skill library grows — the operator answers once, sees they
have answered it before, and writes it down.

## Blocked by 34 because

Without it, an answer or a question can land on a different task than the one
it is about, so the message this ticket surfaces would be attached to the wrong
notification.

## Acceptance criteria

- [ ] A `help_wanted` announcement carries the reporter's last message on that
      task, not only its type and parameters
- [ ] It is still one announcement per thing there is to say — a reworded
      parameter must not produce a second one (this is what nineteen direct
      messages about one report looked like)
- [ ] Nothing is added when the reporter has said nothing since the task opened
- [ ] The reporter's text is treated as untrusted, like everywhere else it
      reaches a prompt or a message
- [ ] A test drives the three-message thread and asserts the operator's
      notification contains the question that was asked
