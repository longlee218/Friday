# 17: A JSON API for the board

**What to build:** Everything the board shows is available over HTTP as JSON, so a
frontend that is not written in Python can render it. The existing server-rendered
board keeps working on the same data throughout — this ticket adds a way in, it does
not take one away.

**Blocked by:** None (can start immediately). **Do this regardless of what happens to
the UI** — it fixes three defects that exist today.

**Status:** ready-for-agent

Three real problems, all found while designing the API and verified in the code:

- `Database.model_calls()` orders by `created_at` **ascending** and then applies
  `LIMIT`, so it returns the *oldest* calls. Past a couple of hundred rows the board
  stops showing the ones anyone would ask about.
- `Database.fail_outbound()` stores `str(exc)` unscrubbed. A provider exception can
  quote an `Authorization` header, and `friday/redact.py` claims to cover exactly
  this path.
- `messages()`, `tasks()` and `outbound()` take no limit at all. The board slices in
  Python, and `Heartbeat.summary()` loads the entire message table every 60 seconds.

The access argument also needs restating rather than inheriting. "Nothing here to
abuse" was about *writes*. Reads were never harmless — this exposes every captured
message and every model prompt — and what actually made it safe was that it only ever
answered on loopback. That has to stay true, or a credential has to appear.

- [ ] A single request returns everything one page render needs, without six round trips
- [ ] Messages can be paged through from newest backwards, and a page size is capped by the server rather than trusted from the caller
- [ ] The prompt and output of a model call are fetched only when someone asks for that one call, never in a list
- [ ] Limits are applied by the database, not by slicing in Python after loading everything
- [ ] Model calls come back newest first
- [ ] Anything that could carry a credential is scrubbed before it leaves the process, including the error recorded against a failed send
- [ ] The API answers only on loopback, or requires a credential — never neither
- [ ] A browser origin other than the API's own can read it, and only the origins that are supposed to
- [ ] The schema is published in a form a TypeScript client can be generated from
- [ ] The existing HTML board still works, unchanged, from the same data
