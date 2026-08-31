# 01: Capture a live mention

**What to build:** Someone tags the watched account in a whitelisted channel, and a
record of that message lands in storage. This is the foundation ticket: it carries the
package skeleton, configuration loading, and the storage layer, because none of them
are demoable on their own.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] A direct mention of the watched account in a whitelisted channel produces exactly one stored event
- [x] A role mention that the watched account holds is captured the same way
- [x] A direct message to the watched account is captured the same way
- [x] A message in a channel outside the whitelist produces nothing
- [x] A message in a whitelisted channel that does not mention the account produces nothing
- [x] The channel whitelist and mention types are read from the structured configuration file, not hardcoded
- [x] Stored events carry author, text, timestamp, mention type, and the platform message id
- [x] All storage access is asynchronous; no blocking database call runs on the event loop
- [x] The conversation each event belongs to is recorded, keyed by platform, channel, and thread

## Comments

Implemented via TDD through the `Provider` seam. 23 tests passing.

Every criterion above is covered by an automated test except **"all storage
access is asynchronous"**, which is a structural property (aiosqlite throughout,
no blocking calls) rather than something a test asserts.

**Not yet verified end to end against live Discord.** The adapter is written but
has never held a real connection — that needs a user token in `.env` and at
least one channel id in `config.yaml`. Until someone runs it and tags the
account, the live half of this ticket is unproven.
