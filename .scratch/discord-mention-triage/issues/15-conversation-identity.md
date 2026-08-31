# 15: Conversation identity, resolved rather than derived

**What to build:** Two chat platforms can hand back the same numeric id without the
system confusing one conversation for another. Everything that keys off a
conversation — context, tasks, the outbox — uses one value that is unambiguous on
its own, and one module owns the rules that produce it.

**Blocked by:** 11 (done). **Do this before 12** — an outbox keyed on a conversation
id that later changes shape means migrating rows that already exist.

**Status:** ready-for-agent

Ticket 11 gave the concept its name and its column. What it did not do is make the
value *identify* anything: `conversation_id` is a bare Discord snowflake. The
provider is dropped on the way in, and `Database.messages(conversation_id)` filters
without it, so a second provider that reuses a number shares a history with the
first. `Conversation` in `friday/models.py` already declares the right identity —
`(provider, channel_id, thread_id)` — and nothing uses it.

The rules that produce the value are also spread across three places: a property on
`InboundEvent`, a `'' vs NULL` special case in the schema, and a DM check inside the
Discord adapter. Each is small; together they are the definition of a conversation,
and it is written down nowhere.

- [ ] Two providers using the same channel or thread id keep separate conversations, separate context, and separate tasks
- [ ] Everything that stores or queries by conversation uses the resolved value, with no caller reconstructing it from parts
- [ ] The rules deciding what counts as one conversation — thread versus parent channel, one-to-one DM versus group — live in one module and are tested there
- [ ] A platform whose threading model differs from Discord's can be added without changing anything above that module
- [ ] The existing database is migrated in place, keeping its messages, decisions and tasks attached to the same conversations
