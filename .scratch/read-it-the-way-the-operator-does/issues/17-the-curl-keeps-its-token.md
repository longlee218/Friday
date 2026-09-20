# 17: The curl keeps its token

**What to build:** `Prepare` replaces the *values* of auth headers in the
reporter's curl before anything else reads it — before the params are
stored, before a prompt is built from them, before an outbox row quotes
them.

**Blocked by:** nothing. **Decisions:** finding C, which ticket 16
confirmed is still owed.
**Status:** ready-for-agent

## Why

Finding C was written as a risk. It is not a risk any more.

- `tasks` rows 4 and 6 hold a reporter's `Authorization: Bearer <JWT>`
  verbatim in `params.curl`.
- Outbox row 22, a `help_wanted` sent to Discord at
  `2026-09-20T04:41:19`, quotes that whole curl — token included.

Neither is a bug in something. It is the gap finding C named, seen from the
live database.

`sensitive_words` does not match `Authorization: Bearer …` (spec, "Seen in
passing"), and that is deliberate: the prefilter decides whether a message
may reach a third-party API at all, and a curl must reach it. `scrub` covers
logs and the board. Nothing covers `tasks.params`, the prompt built from
them, or a row queued out of them.

D11 — no redaction while the agent thinks — stands and does not conflict.
D11 is about the app's own log bodies, which the app already redacts itself
(`"refreshToken":"[REDACTED]"`). The curl is not an app log; it is the
reporter's own credential, pasted by somebody who was not thinking of it as
one.

**Nothing has to be revoked.** Both live tokens are ReelMe `GUEST` tokens
for dev, and both expired on 2026-09-18 at 10:05 UTC (`exp` 1789725926 —
a five-minute token, and the same one in both rows). What has to
change is that the next one is never stored.

## What goes and what stays

Header **names** stay; only values go. `Diagnose` often needs to know that
an `Authorization` header was sent, and never needs its value. Replace with
`[REDACTED]`, the marker the app already uses, so one convention covers
both.

At least: `Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`,
`X-Api-Key`, `Api-Key`.

The request **body** stays whole. It is evidence — `runId`, `skuKey`,
`variantId` in case 1 are what a diagnosis is built from — and a body
carrying a password is a different problem, already the prefilter's.

Where it runs matters more than what it matches: before the params are
written, so nothing downstream has to remember. A redaction applied at the
prompt only would still leave the token in the database and in the outbox
row, which is what happened here.

## Verify

- A test that fails today: extract a task from a curl carrying a Bearer
  token; assert the stored `params.curl` reads `Authorization: Bearer
  [REDACTED]` and that the token string appears nowhere in the row.
- The same assertion on the `help_wanted` row queued from it — the path
  that actually leaked, not a proxy for it.
- A curl with no auth header survives byte for byte, line breaks included.
  The extractor's contract is "verbatim with its line breaks — somebody
  will paste it into a terminal", and redaction must not quietly reformat
  it.
- The guard deleted once and watched go red, per `CLAUDE.md`.

## Backfill

Rows 4 and 6 are the only ones today, and both tokens are dead. Either a
migration rewrites the two `params.curl` values, or the operator deletes the
two tasks. The ticket picks one and says which; this is not worth an
Alembic revision if the operator would rather drop them.
