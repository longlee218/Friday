# 17: The curl keeps its token

**What to build:** `Prepare` replaces the *values* of auth headers in the
reporter's curl before anything else reads it — before the params are
stored, before a prompt is built from them, before an outbox row quotes
them.

**Blocked by:** nothing. **Decisions:** finding C, which ticket 16
confirmed is still owed.
**Status:** done 2026-09-20

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


## Done

**`scrub` already knew the pattern. What was missing was a place it ran.**
`friday/ops/redact.py` has matched `Bearer <token>` and bare JWTs since it
was written, on every log record and on what the model layer stores — and
`artifacts.content`, `tasks.params` and `outbox.text` were none of those.
So this ticket added no pattern. It added two call sites and a migration.

- **At the write.** `_record_artifacts` stores `scrub(body)`. That row is
  what every later reader copies from — the parameter, the extractor's
  prompt, the outbox row that quotes the request, the report file — so
  scrubbing once, at the write, is the only version of this that cannot be
  forgotten at a call site.
- **At the extractor's read.** `original_text_for` scrubs what it returns,
  because a reporter who pastes a request *without* a code fence produces no
  artifact at all, and this is the read that becomes the prompt. That is
  finding C's "not the prompt sent to the provider", closed.
- **The request survives whole**; only the credential goes. Measured on the
  live `af85b208fd70e`: 1155 characters down to 480, every header name kept,
  `"https://…/v1/pod/orders/init"` untouched, `Authorization: [REDACTED]`.

**One deviation from what this ticket asked for.** It asked for
`Authorization: Bearer [REDACTED]`, keeping the scheme. `scrub`'s existing
pattern consumes `Bearer <token>` as one match, so the result is
`Authorization: [REDACTED]`. Narrowing that regex is a change to the one
function standing between this system and the operator's Discord token, for
the sake of one word — not worth it. The header's *name* survives, which is
what `Diagnose` needs: that an `Authorization` header was sent is evidence;
its value never is.

**Backfill: migration `b7c1a4e93f02`**, data only, rewriting
`artifacts.content`, `tasks.params` and `outbox.text` through the same
`scrub`. Run against a copy of the live database: **0 rows of any of the
three still hold a JWT**, down from three artifacts, two tasks and one
outbox row. It does not repair ticket 18's retyped curl — that needs a join
and a judgement about which artifact belongs to which task, and a judgement
does not belong in a migration. The downgrade does nothing and says why: a
credential this removed is gone from the row it was in.

**It reaches the live database on the next `run_agent.py` start**, which
upgrades to head before anything opens the database.

**Verified.** Suite `1336 passed, 1 skipped`. Both call sites deleted once
and watched go red.

## Noticed, not changed

`messages.text` and `messages.original_text` still hold what the reporter
actually typed, token included. That is deliberate and worth stating rather
than fixing quietly: those two columns are the record of what was said, the
board that renders them is loopback-only, and every path that *derives*
anything from them — artifact, parameter, prompt, outbox row, report — is
now scrubbed. Whether the raw record should be scrubbed too is a different
decision, and it is the operator's.
