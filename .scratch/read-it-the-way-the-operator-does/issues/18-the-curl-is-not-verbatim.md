# 18: The curl is not verbatim, it is retyped

**What to build:** `curl` lifted out of the message by code and carried
through extraction by reference, instead of being written back out by the
model token by token.

**Blocked by:** nothing. **Decisions:** none yet — this is a defect found
on 2026-09-20, not a decision already taken.
**Status:** ready-for-agent

## Why

`ApiIssueParams.curl` says what the field is for: "The curl command or raw
request they included, **verbatim** with its line breaks — somebody will
paste it into a terminal."

Task 6 does not hold what the reporter sent. Measured against the row in
`messages` it was extracted from:

| | Bearer token | JWT payload |
|---|---|---|
| The message, `2026-09-20T04:40:46` | 678 chars | `"deviceId":"15A7A881-…"` |
| `tasks` id 6, `params.curl` | 676 chars | `"devieId":"15A7A881-…"` |

One character gone out of the middle of a base64 segment. Same `iat`, same
`exp`, same signature, so it is the same token — retyped, and retyped
wrong.

Task 4, from the same reporter and the same original token, came through
intact. So this is not a rule that always fires; it is a model copying 678
characters of base64 and dropping one, which it will do again on a different
character, in a different run.

## What it costs

A curl that is 99.9% right is worse than no curl:

- Pasted into a terminal, this one fails its signature check and returns
  401. The operator reads that as "the token expired" or "auth is broken"
  and goes looking down a path the reporter never went down.
- Every downstream node believes the field. `FindRequestLog` matching by
  path and timestamp is unaffected, but anything that reads an id, a
  `runId`, a `variantId` out of the body inherits whatever the model typed.
- Ticket 00 case 1 depends on it: the slice's whole premise is reaching the
  reporter's own request.

## Why it happens, and where the fix belongs

`Transform` already gets this right — "split before cleaning", code out
first, never touched, put back where it was (CONTEXT.md, *Transform*) —
because stripping an emoji inside a `curl` corrupts the one part that has
to survive. Extraction then undoes it: the curl goes into a prompt and the
model writes it back out as a JSON string field.

So the fix is the same shape as the one already made a layer up: the model
should say **which** block is the curl, not reproduce it. A block id, an
index into the code blocks `Transform` already split out, and code puts the
verbatim text into the params.

That is the sketch, not the decision. The ticket should confirm against
`friday/text/transform.py` and `friday/extraction/` that the block survives
that far with its identity intact, and say so before building.

**This runs close to a decision already taken, and must not reverse it by
accident.** `friday/text/param_hygiene.py` records that three regex finders
— including `find_curl` — were deleted on purpose: they put two producers
on one field, which is the shape `_merge` existed to reconcile, and `_merge`
is why the operator once got nineteen direct messages about one report.

A block reference is not that. Nobody regexes a curl out of the text and
nobody reconciles two candidates: the model still decides *which* block is
the curl — one producer, one decision — and code does the copying, because
copying is not a judgement. If the ticket finds itself reaching for a
pattern that recognises a curl, it has drifted into the deleted design and
should stop and say so.

## Verify

- A test that fails today: a message carrying a 600-plus-character token in
  a code block; assert `params.curl` equals the block byte for byte.
- A property-ish case: the same message extracted twice gives the same
  `params.curl`. Today two runs can differ, which is also why the
  `extraction_mark` fingerprint cannot be trusted to mean "the same reading".
- Line breaks, quoting and `\` continuations preserved exactly — the field's
  own contract.
- Ordering against 17: 17 redacts the auth header *value* on purpose. This
  ticket's byte-for-byte assertion is against the text 17 hands it, not
  against the raw message. Whichever lands second states that.

## Noticed, not changed

Task 6's stored token being two characters short is what exposed this, and
it was only visible because task 4 held the same token intact to compare
against. There is no check anywhere that a copied-out field still matches
its source. Whether that deserves a general guard, or only this one field,
is the ticket's to answer.
