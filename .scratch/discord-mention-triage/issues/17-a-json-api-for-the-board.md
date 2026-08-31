# 17: A JSON API for the board

**What to build:** Everything the board shows is available over HTTP as JSON, so a
frontend that is not written in Python can render it. The existing server-rendered
board keeps working on the same data throughout — this ticket adds a way in, it does
not take one away.

**Blocked by:** None (can start immediately). **Do this regardless of what happens to
the UI** — it fixes three defects that exist today.

**Status:** done

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

- [x] A single request returns everything one page render needs, without six round trips
- [x] Messages can be paged through from newest backwards, and a page size is capped by the server rather than trusted from the caller
- [x] The prompt and output of a model call are fetched only when someone asks for that one call, never in a list
- [x] Limits are applied by the database, not by slicing in Python after loading everything
- [x] Model calls come back newest first
- [x] Anything that could carry a credential is scrubbed before it leaves the process, including the error recorded against a failed send
- [x] The API answers only on loopback, or requires a credential — never neither
- [x] A browser origin other than the API's own can read it, and only the origins that are supposed to
- [x] The schema is published in a form a TypeScript client can be generated from
- [x] The existing HTML board still works, unchanged, from the same data


## Delivered

Five endpoints. `/api/board` is one aggregate because those four queries are
always rendered together and always want the same instant of the database;
splitting it would buy round trips and nothing else. A model call's prompt is
never in a list — it is large, and it carries whatever a stranger pasted into
the conversation it was assembled from — so it is fetched for one message, on
request.

The three defects are closed. `model_calls()` sorted ascending before limiting,
so it returned the oldest calls rather than the ones anyone would ask about.
`fail_outbound()` stored a provider exception unscrubbed on the one path by
which one reaches the database. And `messages()`, `tasks()` and `outbound()`
took no bound at all — the board sliced in Python and the heartbeat loaded the
whole message table every sixty seconds to print one number, which is now a
`COUNT`.

A fourth thing turned up while fixing the third: **a conversation's context was
unbounded**. Every stored message in a channel went into the triage prompt, so
the cost of classifying grew with the channel. It is now the most recent
`context_messages`, which was already configured and already meant this.

## Scrubbing on the way out as well as in

`_clean()` runs over the whole response rather than the fields believed to be
risky, because the field nobody thought about is the one that leaks. Task
parameters and decision parameters are model-extracted from a stranger's text;
`last_error` began life as a provider exception. It is already scrubbed at write
time, and is scrubbed again here — the cost is a regex over a few kilobytes and
what it prevents is unscoped access to the operator's account.

## The bind

`serve_board` was on `0.0.0.0`. On a VPS that publishes every captured message
and every model prompt to anything that can reach the port.

`check_exposure` now refuses to start on a non-loopback host unless
`BOARD_TOKEN` is set. The design's "nothing here to abuse" was always an
argument about *writes*; reading was never harmless, and what made it safe was
loopback. Binding wider is a decision, and it now has to be made on purpose.
