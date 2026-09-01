# 06: Approve in Discord and reply as you

**What to build:** A task whose work is finished asks for approval in chat; approving it
posts the reply publicly under the watched account's own identity. This is the first
ticket that writes to a channel anyone else can see.

**Blocked by:** 05, 12

**Status:** done

- [x] A task reaching the review state sends a direct message containing the proposed reply text and approve/reject controls
- [x] Approving posts that reply into the originating conversation, appearing as the watched account rather than as an application
- [x] Rejecting returns the task for human input instead of posting
- [x] The approval card is itself an outbound row, so a card that fails to send is visible rather than leaving the task waiting for a decision nobody was asked for
- [x] The reply action refuses to run when the task has not been approved, regardless of which node calls it
- [~] *Obsolete.* That refusal is returned to the caller as an ordinary result the
  model can respond to, not raised as an error — written when the reply action was a
  `post_reply()` tool the model called. Ticket 12 replaced that with an outbound row
  and a predicate in `sendable_outbound`'s query, so there is no caller to refuse:
  an unapproved reply is simply not selected. The criterion has no subject any more.
- [x] Who approved and when is recorded against the task
- [x] The approval prompt reaches the human without requiring the application identity to be present in the watched channels


## Verified against the live bot before any of it was written

- It logs in on `Intents.none()`. The `members` intent is privileged and was
  refused; it is also not needed, because this identity reads nothing.
- It shares **no guild** with the operator and still opens a direct message to
  them. That is the criterion about not needing to be in the watched channels,
  answered by measurement rather than by assumption.

## Delivered

The card is an outbound row like any other, so it inherits retries and shows up
on the board if it cannot be delivered — a card that fails silently leaves a
task waiting for a decision nobody was asked for.

The refusal is structural rather than a check. `sendable_outbound` joins the
task and will not select a `reply` until `approved_at` is set, so there is no
"reply action" to call wrongly: approving records who and when, and the reply
becomes selectable on its own.

The task sits in **`review`** while it waits. `waiting_for_details` means
waiting on the reporter; this is waiting on the operator. Different people,
different columns on the board, different thing to chase.

Button ids carry the task, and the view is registered again at startup, because
a decision may be made hours later across a redeploy — nothing about it lives in
this process's memory.

## One criterion could not be met as written

> That refusal is returned to the caller as an ordinary result the model can
> respond to, not raised as an error

There is no caller and no model. That wording belongs to the superseded design
where a node called a `post_reply()` tool and had to be told no. Under the
design that shipped, a reply is a row and an unapproved one is simply not
selected — there is nothing to refuse, so nothing to phrase as a tool result.

## Not exercised until the responder is on

Cards only accompany drafts, and drafts only exist when `use_responder` is true.
With it off, the template path runs exactly as before and no card is ever made.


## What the operator is interrupted for — corrected after the fact

An earlier pass had a model-written request for details go out as a `reply`,
needing approval, on the reasoning that the template was allowed out unreviewed
only because it was the same sentence every time.

That was wrong, and the operator said so: **the risk is in answering, not in
asking.** A request for a correlationId is harmless however it is phrased, and
routing it through approval buys nothing while costing an interruption and a
delay on the one thing the agent currently does well.

So the bot interrupts for exactly two things:

| | |
| --- | --- |
| **Something needs approving** | a proposed reply, with buttons, not public until answered |
| **Something cannot be handled** | a statement, no buttons — there is no decision to make |

Everything else, including asking for missing details in the operator's own
voice, is the agent's own call.

The second of those did not exist before. A task reaching `needs_human` sat in a
column nobody was watching, which is the same as losing it. It is now announced
once — the outbound row is itself the record of having said it, so a
notification that repeats until it is ignored cannot happen.
