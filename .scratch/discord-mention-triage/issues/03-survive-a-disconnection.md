# 03: Survive a disconnection

**What to build:** Messages that arrive while the connection is down are still captured
once it returns, and no message is ever captured twice. This is the reliability layer:
a live connection alone loses anything sent during an outage.

Disconnections come in two kinds and must not be treated alike. A network drop is
transient and should be retried. A rejected credential is permanent — retrying it
forever produces a process that looks healthy while silently receiving nothing,
which is the worst failure this system can have.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] A brief disconnection followed by a reconnect loses no messages
- [x] An outage long enough to invalidate the session still recovers the missed messages via the periodic sweep
- [x] A message delivered by both the live connection and the sweep produces exactly one stored event
- [x] The sweep runs on a timer as well as on reconnect, so a stalled-but-open connection is still covered
- [x] Resume state and per-channel position survive a process restart; the service does not re-process history from the beginning
- [x] The sweep is scoped to whitelisted channels and does not fan out across everything visible
- [ ] A transient failure (dropped network, unexpected close) reconnects with backoff and recovers the gap
- [x] A rejected credential stops reconnection instead of retrying, and surfaces unmistakably — a dead token must never present as a quiet channel
- [x] The two failure classes are distinguishable in logs, so an operator can tell 'retrying' from 'needs a human'
- [x] Each watched channel records how far it has been read, advancing on every message seen and never moving backward
- [ ] A session's recent history is fetched once when it produces its first mention, and its messages are persisted from then on
- [x] One-to-one DMs and threads are knowingly not swept; this is recorded, not silently missing

## Comments

Four slices done, 56 tests. Both delivery paths converge on one acceptance
method — advance cursor, scope, deduplicate, record — which is *why* a message
arriving on both produces exactly one event, rather than needing a separate
guard.

**Design change:** persisted `session_id`/`seq` was cut from `DESIGN.md`. The
library owns them in memory and reconnects on its own; Discord will not resume a
stale session after a restart anyway, and the cursor plus the sweep close that
gap for less code.

**Still open — the conversation context slice.** Seeding a session's recent
history on its first mention, then persisting its messages, is specified but not
built. Ticket 04 needs it.

**Library-provided, not covered by tests:** reconnection with backoff and RESUME
replay are `Client.start(reconnect=True)`. Whether they behave as documented,
and whether `history(after=...)` paginates correctly against real Discord, can
only be proven by a live run.
