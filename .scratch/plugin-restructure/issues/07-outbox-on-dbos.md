# 07: Outbox as a DBOS workflow

**What to build:** The outbox delivery loop runs as a DBOS workflow — an approved reply is never posted twice across a crash and never silently lost.

**Blocked by:** 06.

**Status:** ready-for-agent

- [ ] Delivery is a DBOS step carrying an idempotency key (passed down when the channel supports one)
- [ ] `dispatching` is written before the channel call
- [ ] A send interrupted mid-call becomes `delivery_unknown` at startup and goes to the operator, never auto-retried
- [ ] `approved_payload_hash` checked at dispatch; any change voids the approval; the staleness check still runs
- [ ] Kinds that need no approval take the `policy_approved` edge, hashed at enqueue
- [ ] Test: kill between the channel call and the write → the row is `delivery_unknown` after restart and nothing is sent twice
- [ ] `uv run pytest -q` passes
