# 34: A reply belongs to the task it answers

**What to build:** When the reporter replies to a question the agent asked, that
reply goes to the task the question was about — whatever triage decides the
reply *is*. Answering "correlationId là cái gì a nhỉ" must not close the bug
report and open a documentation task; supplying the id must not open a second
report.

**Blocked by:** None (can start immediately)

**Status:** done

## Why this is not already true

A reply that lands on an open task goes through the follow-up path, which
decides by **type**: same type means more detail about the same work, a
different type means the subject changed and a person should look. That rule is
right for a conversation that drifts. It is wrong for an answer to our own
question, and it is decided by the classifier — which does not agree with
itself between runs.

Observed, live, in one thread:

    them:  a Long ơi a kiểm tra API giúp e e thấy bị 500
    us:    em gửi anh cái correlationId hoặc curl em gọi được không?
    them:  correlationId là cái gì a nhỉ, e ko biết

That third message was classified `api_issue`, matched the open task, and
behaved correctly — by luck. Classified `doc_question`, it would have pushed
the bug report to `needs_human` and opened a second task for a question that is
about the first one.

## The rule

**A reply names the message it answers.** When that message is one this system
sent, the outbound row that produced it already records which task it was
about. So "which task does this belong to?" has a deterministic answer that
needs no model and cannot drift.

Type is still the right question for an unprompted message in a conversation
with work in flight. It is the wrong question for an answer.

## Acceptance criteria

- [x] A reply to a message the agent sent is worked as a follow-up of that
      message's task, whatever type triage assigns it
- [x] A reply whose target belongs to no task (a liveness alert, the daily
      summary) falls back to today's behaviour rather than erroring
- [x] An unprompted message in a conversation with work in flight still goes
      through the type check — this narrows when the type check applies, it
      does not remove it
- [x] A message replying to somebody else is unaffected
- [x] A test drives the three-message thread above with triage forced to
      classify the question as `doc_question`, and the bug report stays open
      with no second task

One criterion was added while building: **a reply does not reopen work
somebody closed.** The lookup only returns an open task — a "cảm ơn anh" on a
finished thread opens its own task rather than resurrecting the old one.

Each of the three guards was checked by removing it and watching the suite go
red: the lookup itself, the open-task filter, and the branch that skips the
type check.
