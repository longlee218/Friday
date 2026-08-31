# 16: Reply in the operator's voice

**What to build:** A task that has been worked out produces a written reply that
sounds like the operator, shown to them for approval before anyone else sees it.
This is the second agent, and the first thing that speaks to other people in their
name.

**Blocked by:** 12, 06. Unblocks 14 — the responder is the second agent, which is
what turns the harness from a hypothetical seam into a real one.

**Status:** done

Tone comes from **few-shot examples of the operator's real past replies**, not a
written style guide: real examples carry a voice that description does not. This is
why their own messages are retained as context while still being skipped as
triggers — a stored conversation missing one side of itself teaches nothing.

The hard part is not the writing, it is **staleness**. A draft is written against a
conversation that keeps moving, and approval happens minutes or hours later. Posting
an answer to a question that has since been withdrawn, corrected, or already
answered by someone else is worse than posting nothing.

- [x] A reply is drafted for a task that is ready for one, in the operator's voice, learned from their own past messages rather than from a description of their style
- [x] Nothing reaches a channel without approval
- [x] A draft records the state of the conversation it was written against
- [x] Approving a draft that newer messages have overtaken does not post it — the task returns for rework instead, so posting a stale answer is impossible rather than unlikely
- [x] A burst of follow-up messages does not produce a draft per message
- [x] Direct posting without approval can be enabled per task type on evidence, without reworking the flow — the request for missing details is the first candidate, once a run of them has been approved unchanged


## Delivered so far

`friday/responder/` is the second agent. It is given examples of how the
operator actually writes, the conversation so far, and what needs saying, and
writes that message their way. Proved live against MiniMax M3: given a realistic
corpus of replies, the English template

> Could you send which environment you're on and the correlationId, or the curl
> you used? I'll trace it from there.

came back as

> env nào em đang gọi với correlationId hoặc curl gửi anh để trace thử

**A drafted message is a `reply` and waits for approval.** The template is
allowed out unreviewed because it is the same sentence every time — an argument
that does not survive a model writing it. So the responder is off by default and
changes nothing until it is switched on.

**A responder that cannot answer falls back to the template.** A wrong reply in
someone's name is worse than a plain one, and silence is worse than both.

## Two things the live run found

**Reasoning was in the output.** MiniMax M3 returns a `<think>` block, and the
SDK does not strip it. Unstripped, the model's deliberation about the operator's
colleagues would have been posted publicly under the operator's own name. An
unclosed tag — a truncated response — yields no draft at all, because everything
after it is still working.

**The tone corpus in the live database is the wrong voice.** Every `is_own`
message there is a self-tagged test message written while playing the reporter,
so there is no example of the operator actually *answering* anyone. The model
said so in its own reasoning before that was stripped, and then echoed the
English template. That is testing pollution from `capture_own_messages`, not a
defect: the same code with six realistic replies produced the Vietnamese above.

## Still open

Staleness, debounce, and per-type direct posting. All three need an approval to
exist before they can be exercised end to end, which is ticket 06.


## Staleness and debounce

**A burst does not become three replies.** Someone typing "vẫn lỗi", "alo", "?"
in ten seconds is one person waiting, and each of those messages sends the task
back to be re-planned. The pause is a pause, not a mute: the task stays pending
and is answered on the next pass once the burst has passed.

**An answer the conversation has moved past is not posted.** Checked at the last
moment before it goes out, because that is the only moment at which the answer
is true or false. A stale one fails the row and returns the task to a human
rather than posting something already withdrawn, corrected, or answered by
someone else.

Only an *answer* goes stale. Asking for a correlationId is still worth asking
whatever else has been said since, and is explicitly exempt.

## What has no producer yet, and why that is honest

Nothing currently creates a `reply`. The operator's correction — that the risk
is in answering, not in asking — means a request for details is the agent's own
decision, and answering is the one thing the agent cannot yet do: tracing is not
built, and neither is granting access.

So the approval path and the staleness guard are correct and unexercised. That
is the right order. Building the guard after something depends on it means
building it under pressure, against a bug, in a system that is already posting.
