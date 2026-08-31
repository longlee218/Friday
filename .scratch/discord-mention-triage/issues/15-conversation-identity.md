# 15: Conversation identity, resolved rather than derived

**What to build:** Two chat platforms can hand back the same numeric id without the
system confusing one conversation for another. Everything that keys off a
conversation — context, tasks, the outbox — uses one value that is unambiguous on
its own, and one module owns the rules that produce it.

**Blocked by:** 11 (done). **Do this before 12** — an outbox keyed on a conversation
id that later changes shape means migrating rows that already exist.

**Status:** done

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

- [x] Two providers using the same channel or thread id keep separate conversations, separate context, and separate tasks
- [x] Everything that stores or queries by conversation uses the resolved value, with no caller reconstructing it from parts
- [x] The rules deciding what counts as one conversation — thread versus parent channel — live in one module and are tested there. *One-to-one DM versus group turned out not to be one of them: for identity both are simply channels. That distinction decides a mention **type**, and stays in the Discord adapter where the platform knowledge is.*
- [x] A platform whose threading model differs from Discord's can be added without changing anything above that module
- [x] The existing database is migrated in place, keeping its messages, decisions and tasks attached to the same conversations


## Delivered

`friday/conversation.py` holds `ConversationId` and `resolve()`. An id is
`provider:channel` or `provider:channel/thread`, and that text form *is* the
identity, because it is one column. `models.Conversation` is gone — it was the
same concept without the provider in it, and two representations of one thing is
how they drift.

`InboundEvent.conversation` delegates rather than deriving. `Database`,
`TriageRunner`, `WorkflowRunner` and the `Provider` protocol all pass the
resolved value; nothing rebuilds it from parts. `ConversationId.target_id`
answers the one question a provider still needs — where a reply actually goes —
so the thread-versus-channel rule is not re-implemented at the send site.

## The migration is the work

Autogenerate saw only the shape change to `conversations`. It could not see
that every stored id needed a provider on the front, and had no way to derive
the value for `tasks`, which carries no channel or thread of its own — those
rows are mapped through the messages they came from.

Both directions had the same ordering trap: the table being joined on must not
have been rewritten yet, so `tasks` updates before `messages` in *both* upgrade
and downgrade. The downgrade failed with a NOT NULL violation the first time
for exactly that reason, which is the argument for writing downgrades and
running them rather than leaving a stub.

Verified as a round trip against a copy of the live database — upgrade,
downgrade, upgrade — with 16 messages and 2 tasks intact each time, then applied
to the real one (backup at `data/friday.db.bak-pre-ticket15`).
