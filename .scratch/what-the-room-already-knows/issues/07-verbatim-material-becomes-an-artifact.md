# 07: Verbatim material becomes an artifact the build points at

**What to build:** Code, stack traces, SQL, logs and configuration are stored
whole and pointed at, never summarised. A reporter's `curl` has to reach the
person who will run it exactly as typed, with its line breaks, after any amount
of summarisation has happened around it — and a correlation id is matched by
machine, so a paraphrase of it resolves to nothing.

The module that splits code out of prose already exists for this reason, and its
docstring names the extractor as the cause.

**Blocked by:** 01

**Decisions:** D2, D8

**Status:** done

- [x] Material split out of prose is stored as an artifact with an id and one
      line of description
- [x] The build renders an artifact as its id and that line; an artifact
      belonging to the current task is inlined whole
- [x] No summariser is ever shown an artifact's content, and a test says so
- [x] The reader of an artifact cannot itself produce one, so a large artifact
      read back cannot spill into a second
- [x] A `curl` a reporter pasted survives with its line breaks end to end
- [x] A correlation id reaches the parameters character for character
- [x] Guards deleted once and watched go red

## Comments

**The seam already existed, half-built.** `friday/text/transform.py` has
split code out of a message's prose since it was written, and
`InboundEvent.code` has carried the result since ticket 02 — but nothing
ever read `.code`: it reached `record_message` and stopped, a producer with
no consumer, its own docstring already saying "for anything that wants the
code without the prose around it" with nothing that did. This ticket is the
consumer.

**Two stores, one new: `artifacts`, and one new column on `messages`.**
`Database._record_artifacts` runs once, right after a message is recorded
for the first time (gated on `inserted`, not merely on `event.code`, so the
two delivery paths recording the same message twice — CLAUDE.md's dedup
rule — cannot double the artifacts). Each span in `event.code` becomes its
own `Artifact` row: `content` untouched, `description` built from shape and
size alone (kind — curl / stack trace / SQL / code — plus line and
character counts), never from the content's own bytes. `messages.redacted_text`
is the message's `text` with each span replaced by `[artifact id:
description]`, computed by a new `friday.text.transform.redact` — the same
split `transform` does, restoring references instead of the code.

**A mutation caught the description leaking the content it exists to hide.**
A `curl` is usually one line, and "the first line, capped" *is* the whole
artifact when the artifact is one line — exactly the leak this ticket exists
to close, since the description is what a summariser is shown. Found by the
mutation sweep below, not written correctly the first time: the description
is metadata only now (`"a curl, 1 line, 47 chars"`), and a test pins that
neither the curl nor anything inside it appears in the description, however
short the content is.

**"The build" is two builds, not one, because that is what the codebase
actually has.** D3/D4's unified context builder is not implemented as code
yet — no ticket on this board has built a `BuiltContext` class, and D4 is
not among this ticket's own decisions. What exists are two already-separate
read paths that happen to satisfy the checklist's two halves without needing
to be the same mechanism: `relevant_messages_in_channel` — the summariser's
own read, its only caller — now substitutes `redacted_text` for `text`,
giving the summariser id-and-description only. `original_text_for` — node
0's own read, the extractor's — was never touched, so a task's own material
reaches it exactly as before: whole, with its line breaks, character for
character. "An artifact belonging to the current task is inlined whole" is
true of that path by construction rather than by a new inlining step,
because it never redacted in the first place. Every other reader of `text`
(`messages`, `relevant_messages`, triage, the responder's tone examples, the
Rooms screen) is unaffected for the same reason — nothing about what they
read changed. This is the scope call worth recording, the way ticket 10
recorded `memory_supersede` having no tool yet: an affirmative regression
test (`test_every_other_reader_of_text_is_unaffected`,
`test_the_current_tasks_own_build_still_sees_the_curl_whole`) stands in for
a new "inline" mechanism that the architecture does not currently need.

**"The reader of an artifact may not itself produce one."** Structurally
true here: nothing calls `transform`/`redact` on an `Artifact.content`
anywhere in this diff — `_record_artifacts` runs once, on the message that
produced the code, never again. Locked in with a spy on `redact` counting
exactly one call per recorded message, on the message's own text.

**A tie the first version of `_record_artifacts` did not account for.**
Several artifacts from one message shared the same `created_at` (`_now()`
called once), so `artifacts_for_message`'s claimed ordering — the same order
`event.code` lists them in — depended on SQLite breaking the tie by
insertion order, which nothing asked for. A mutation reversing the read's
`ORDER BY` passed the suite regardless, revealing the guard was untested.
Fixed by spacing each artifact a microsecond apart; the same mutation now
fails correctly.

**No board change.** Unlike ticket 10, nothing in this ticket's checklist
asks the operator to see artifacts, and no other ticket on this board wires
one — `SUMMARY_FIELDS` still excludes `artifacts` (`channel_context.py`'s
own comment: "waits for ticket 07, which is what produces one" — read as
this ticket unblocking a later wiring, not requiring it itself, since D9
and the summary schema are not among this ticket's decisions either).

**The classifier evaluation.** Not run, same judgement as tickets 01 and 10:
`friday/text/transform.py`'s public `transform()` is behaviourally
unchanged (all 17 pre-existing tests pass unmodified) and nothing in
`friday/triage/`'s own files changed. `redacted_text`/`artifacts` are new,
unread by triage. Triage's prompt is byte-identical.

**Guards, deleted once and watched go red, synchronously — six of them:**
`redact`'s ref-count check, the description's content-hiding, the
summariser's `redacted_text` substitution, the dedup gate on artifact
creation, the artifact-ordering fix (caught only after correcting the
timestamp tie), and `redact`'s split agreeing with `transform`'s. Each
caught its own test and nothing else, restored and diffed against a
pre-mutation backup before the next.

**Suite: 1043 passed, 1 skipped** (up from 1025 before this ticket — 18 new
tests, 6 for `redact` in `test_transform.py` and 12 in a new
`tests/test_artifacts.py`).

## Review

Two-axis review against `22585a1` (ticket 10's commit). Both axes found the
same real defect from two directions, plus a genuine documentation gap.

**Standards: `_record_artifacts`'s docstring overclaimed, and the crash
window it implied away was real.** `redact` re-splits `event.text` — code
already restored into place, not the original raw message — and the
docstring said this "agrees" with the first split as a settled fact. It
is not: `transform`'s fenced regex is non-greedy, and content whose own
body contains a literal triple backtick can in principle make the second
split disagree with the first. A hand-built case and a 20,000-trial random
search over backtick-heavy content found no actual disagreement — the
mechanism turns out to be more robust in practice than the theoretical
concern suggested — but nothing in the regex rules it out, so the
docstring's "proven" was not earned. Standards also found the real
consequence of trusting it anyway: a disagreement would have raised
`ValueError` **after** the message row was already committed in a separate
transaction, and — worse — a crash between that commit and the artifact
write would silently leave `redacted_text NULL`, which
`relevant_messages_in_channel`'s own fallback reads as "nothing to redact"
rather than "redaction failed", showing the summariser raw content in
exactly the case D8 exists to prevent.

Fixed: `_record_artifacts` now catches `ValueError` from `redact`, logs a
warning naming the message, and returns — leaving that one message exactly
as if it had carried no code at all (no artifacts, `redacted_text` stays
`NULL`, every reader including the summariser falls back to `text`). Not a
silent corruption of a different message's redaction; a narrow, logged,
self-limiting gap on the one message it happens to. The docstring no longer
claims a proof it cannot back, and separately documents the same accepted
gap for a process crash between the two writes — the two failure shapes
degrade to the same fallback. A new test forces the `ValueError` via
monkeypatch (rather than relying on ever finding a naturally occurring
trigger) and asserts the message is still recorded, with no artifacts and
the raw-text fallback in place — then mutation-verified by removing the
`try/except` and watching it fail.

**Standards, minor: a filter written twice.** `relevant_messages_in_channel`
built `provider == provider, channel_id == channel_id` twice, once through
`_relevant(...)` and once inline for the `redacted_text` lookup. Factored
into one `scope` tuple both queries share.

**Spec: the correlationId claim was untested past the raw-text boundary.**
The checklist says a correlation id "reaches the parameters" — the existing
test proved only that it survives into `original_text_for`'s return value,
never into an actual `Params` object. Added
`test_a_correlation_id_reaches_the_params_object_itself`, which drives a
real `Extractor.run()` — the same seam `tests/test_extraction.py`'s own
end-to-end tests use — with a stub harness that echoes the id back as a
correct extraction would, and asserts both that the id reached the model's
own input unaltered and that the resulting `Params.correlation_id` equals
it exactly. This is the boundary the checklist names; whether a live model
correctly *copies* it is a different question, covered by
`CLAUDE.md`'s own eval rule for triage, not this ticket's.

Full suite re-run after both fixes: 1045 passed, 1 skipped.
