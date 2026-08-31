# 11: One message table

**What to build:** Every message the system has seen lives in one place, whether it
addressed the operator or not. Nothing behaves differently; the same mentions become
the same tasks. What changes is that a column added to a message exists once.

**Blocked by:** 04

**Status:** done

`events` and `messages` are near-duplicates today. Every in-scope mention is written
to both, so a column added to one silently goes missing from the other — which is how
`is_own` came to report `False` in one table and `True` in the other. Under the
domain model there is one kind of thing, a **message**, and `mention_type` is what
separates the triage queue from the surrounding context.

The same pass settles a name collision before it lands: `friday.models.Session` means
*a conversation*, while the Agents SDK's `Session` means *an agent's own transcript*.
Once agent sessions exist, one of them has to move. It should be ours.

- [x] A message that addresses the operator and one that does not are stored in the same table, distinguished by mention type
- [x] The triage queue is a query over that table, not a separate table
- [x] A triage decision is recorded against the message it was made about
- [x] Conversation context is read from the same table the queue reads from
- [x] An existing database is migrated in place, keeping its messages and decisions
- [x] The conversation concept is named `Conversation` throughout, leaving `Session` free for the SDK's meaning
- [x] No behaviour changes: the same messages produce the same tasks, and the test suite proves it before and after
