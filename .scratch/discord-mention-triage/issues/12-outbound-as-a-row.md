# 12: Outbound as a row

**What to build:** Deciding what to say stops being the same act as saying it. A
workflow produces something to send; a separate loop delivers it, retries it, and
surfaces what it could not deliver. The reply you get today for a missing
correlationId still arrives, by the new path.

**Blocked by:** 04

**Status:** ready-for-agent

Today `WorkflowRunner` holds a `Provider` and calls `send()`, so workflow logic is
coupled to a chat platform and there is no point at which an outbound message can be
approved, retried, audited or held. `plan_api_issue()` already returns an intent —
the runner dissolves it into a platform call one line later.

Approval is a fact about the **task**. The sender joins it rather than each caller
checking it, so an unapproved reply has no path out. `kind` decides which rows need
that approval at all: asking for a missing parameter is the system completing a
task's own fields, not the agent speaking for the operator.

- [ ] A workflow produces an outbound intent and never touches a provider
- [ ] Workflow tests run with no provider present
- [ ] Delivery runs as its own loop, so a slow or rate-limited send never stalls the loop that decides what to do next
- [ ] An intent whose kind requires approval is not delivered while its task lacks one, and that is enforced where rows are selected rather than by each caller
- [ ] An intent that asks for missing task parameters is delivered without waiting for approval
- [ ] A row names which identity sends it, and what it replies to
- [ ] A failed send is retried up to a configured bound, with backoff
- [ ] A send that exhausts its retries leaves the row failed, its error recorded, and its task waiting for a human
- [ ] Confirming a failed row was sent by hand records that, distinguishably from having abandoned it
- [ ] The existing missing-details reply still goes out, through the new path, under the same configuration flag
