# 05: The unreviewed ask is checked before it speaks

**What to build:** A predicate over the responder's reworded ask. It passes, it
goes out; it fails, the template goes out.

**Blocked by:** None (can start immediately)

**Decisions:** D8

**Status:** done

## Why

`auto_ask_for_details: true` is the one message allowed out with no approval,
and `config.yaml` justifies it in one sentence: *"what is being asked never
changes, only the wording does, and the responder falls back to the plain
template if it fails to write one."*

The first half is not enforced anywhere. `Pool._in_the_operators_voice`
(`friday/tasks/pool.py`) calls `responder.draft(...)` and returns `draft.text`
unchanged. The fallback covers `None` — the model failing — and nothing covers
the model succeeding at writing something else.

The responder's input includes `await self._db.relevant_messages(...)`: text
other people wrote in the channel. The trust boundary work (tickets 06, 07 on
the other board) makes that text *inert as prompt structure*; it does nothing
about the text the model then writes. So the one path with no human in it is
also the path whose wording is model-authored from untrusted input, sent under
the operator's name.

This has already happened once in a milder form, and the incident is recorded
in `Responder.draft`'s own docstring: asked to request a correlationId, the
model found one belonging to a different report and wrote "ok có correlationId
rồi, để anh trace thử" — a promise nobody would keep.

The rule this follows is the one `friday/dag/prepare.py` already states for
validation: code is the floor, and a model's contribution is accepted only
where code has nothing to object to.

## Acceptance criteria

- [x] A predicate the ask must pass: still names every field the template asks
      for, no URLs, no code blocks, under a length bound, no commitment verbs
- [x] A draft that fails it is discarded and the template is sent — logged at
      info with the reason, since a silent fallback hides a prompt regression
- [x] Driven at `Pool.run_once` with a responder stub returning hostile text,
      asserting the queued row holds the template
- [x] The sentence in `config.yaml` is rewritten to say what enforces it
- [x] Each guard is deleted once and watched go red

## What it came to

`friday/responder/check.py`, beside the prompt it holds to rather than in the
pool that calls it, on the operator's call: what it enforces is exactly what
`friday/responder/prompt.py` promises, and a rule that lives next to that
prompt is one somebody editing the prompt will see.

Five rules, and the blunt one is the one the incident called for: a list of
promising phrases in Vietnamese and English. "ok có correlationId rồi, để anh
trace thử" is short, has no link, and names the field — every other rule here
would have passed it. False positives are expected: "để anh hỏi lại team nhé"
is an ordinary sentence and will be refused. That is the trade, and it is the
right way round, because failing sends a plainer question and passing wrongly
sends the operator's colleagues something the operator did not say.

**Two rules the existing tests corrected before the review could.** The
technical-word rule demanded *every* kept word from the template, which
refused a draft answering half of an either-or — "the correlationId, or the
curl you used" is an offer of a choice, and taking one of them is the point of
offering it. And it read those words out of the whole template including the
parenthetical `_question_from_clarify` appends to say *why* the question is
worth asking, so a draft was refused for not repeating the reasoning. One of
them, not all, and not from the reason clause.

Both were caught by tests that already existed and had nothing to do with this
ticket, which is the argument for driving the new rule through `Pool.run_once`
rather than only through the predicate: the check in isolation would have
agreed with itself.

730 tests pass (720 before, +10). Six guards, each deleted once and watched go
red — five rules and the call site, because a floor nobody stands on is not a
floor.

## What the review changed

Four of five findings needed code. All were in rules I had already corrected
once, which is the pattern worth naming: each fix was aimed at the case in
front of me and none of them at the shape of the input.

**The reason clause was still read on the other path.** The strip was anchored
to the end of the string because `_question_from_clarify` ends with `)`.
`_question` puts a validation rule's message *inline* and ends with `?`, so it
was never stripped there — and the plainest correct rewording of "which
environment you're on (must be one of: dev, production, staging)" was refused
for not reciting the enum. Reachable exactly when the reporter wrote `sản
xuất`, which CLAUDE.md names as the expected case. Everything from the first
bracket on is reason now, because both builders put the reason last and
nothing else there.

**And a bracket inside the model's own `because` re-armed it.** `because` is
free text an extractor wrote; a strip that stops at the first `)` leaves half
the clause behind. Same bug, different input, which is why the fix is
positional rather than a balanced pair.

**`HTTPS://` was not a link.** The test was case-sensitive and ran against the
raw draft rather than the folded text — a one-character evasion producing a
real, clickable link in a message sent under the operator's name.

**And `để` written decomposed was not a promise.** Vietnamese has two Unicode
spellings of every accented letter and they compare unequal, so the word list
could be walked around by a spelling no editor shows and no reader would see.
Everything is NFC-folded before matching now, which is what makes a word list
a list of words rather than of bytes.

**The fifth is a limit, not a bug, and is now written down.** The
name-what-it-named rule is a no-op when the template names nothing
untranslatable, which is four of the seven questions this system asks. There
is no way to tell a faithful Vietnamese rewording of "what access you need"
from a different question, so the other four rules carry those. CLAUDE.md and
`config.yaml` said it unconditionally; both now say which questions it binds,
and a test pins the limit so the docs cannot quietly outgrow the code.

735 tests pass (730 before, +5). Nine guards, each deleted once and watched go
red.
