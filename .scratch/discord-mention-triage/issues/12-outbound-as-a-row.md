# 12: Outbound as a row

**What to build:** Deciding what to say stops being the same act as saying it. A
workflow produces something to send; a separate loop delivers it, retries it, and
surfaces what it could not deliver. The reply you get today for a missing
correlationId still arrives, by the new path.

**Blocked by:** 04

**Status:** done

Today `WorkflowRunner` holds a `Provider` and calls `send()`, so workflow logic is
coupled to a chat platform and there is no point at which an outbound message can be
approved, retried, audited or held. `plan_api_issue()` already returns an intent —
the runner dissolves it into a platform call one line later.

Approval is a fact about the **task**. The sender joins it rather than each caller
checking it, so an unapproved reply has no path out. `kind` decides which rows need
that approval at all: asking for a missing parameter is the system completing a
task's own fields, not the agent speaking for the operator.

- [x] A workflow produces an outbound intent and never touches a provider
- [x] Workflow tests run with no provider present
- [x] Delivery runs as its own loop, so a slow or rate-limited send never stalls the loop that decides what to do next
- [x] An intent whose kind requires approval is not delivered while its task lacks one, and that is enforced where rows are selected rather than by each caller
- [x] An intent that asks for missing task parameters is delivered without waiting for approval
- [x] A row names which identity sends it, and what it replies to
- [x] A failed send is retried up to a configured bound, with backoff
- [x] A send that exhausts its retries leaves the row failed, its error recorded, and its task waiting for a human
- [x] Confirming a failed row was sent by hand records that, distinguishably from having abandoned it — *the mechanism exists and is tested (`sent_manually`); the Discord button that calls it is ticket 06*
- [x] The existing missing-details reply still goes out, through the new path, under the same configuration flag


## Delivered

`friday/outbox/` is the only module that delivers. A workflow returns an
intent, `WorkflowRunner` writes a row, and the Outbox runs as its own loop
beside ingest, triage and workflows — a rate-limited send inside the loop that
decides what to do next would stall the deciding.

**The approval guard is a `WHERE` clause.** `Database.sendable_outbound` joins
the task and refuses to select a kind that needs approval until
`tasks.approved_at` is set. A caller cannot forget a check it never makes, and
adding a second consumer of the outbox later inherits the guard for free.

**`kind` decides whether approval applies**, so there is no second flag to keep
in step. `ask_for_details` completes the task's own required parameters,
`approval_card` *is* the request for approval and waiting for one would
deadlock, `reply` is the agent speaking in the operator's name and is the only
kind that waits.

**Delivery is at-least-once.** A row is marked sent after the API call, never
before. A crash in between may post twice; the other order loses an approved
reply in silence, and a lost reply is indistinguishable from the system working.

Retries are bounded and back off by doubling, held back by a `retry_after` on
the row rather than by sleeping in the loop. On exhaustion — or on a sender name
that does not exist, which no amount of retrying will fix — the row goes
`failed` with its error, keeps its text so it can be copied, and its task goes
to `needs_human`. A message nobody can deliver is work, and it has to look like
work.

## What is deliberately not here

`Kind.APPROVAL_CARD` exists and nothing creates one. Approving, and the button
that confirms a failed row was sent by hand, are ticket 06 — this ticket built
the road they drive on. `auto_ask_for_details` still gates whether the request
for missing details is written at all, unchanged.

## A correction to the design conversation

Q7 asked whether `sender` should be a provider name or a role, and the answer
was provider name. The question was framed wrongly: `DiscordUserProvider.name`
is `discord`, and it has to stay that way because the bot and the user account
speak into the *same* conversations. So a conversation names a **platform** and
a sender names an **identity** — two namespaces. The decision stands as made,
a flat registry of concrete names; the registry is built at the composition
root rather than read off `Provider.name`.
