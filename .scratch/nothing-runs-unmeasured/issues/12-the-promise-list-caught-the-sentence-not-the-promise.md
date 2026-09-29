# 12: The promise list caught the sentence, not the promise

**What to build:** A rule in `friday/responder/check.py` that refuses a draft
naming work the question did not name — replacing a phrase list that has
already been walked around in production.

**Blocked by:** None

**Decisions:** D8 (ticket 05's own), and the asymmetry it rests on

**Status:** done

## Why

Ticket 05 built the floor under `auto_ask_for_details`, the one message this
system sends with no person reading it first. Five rules; the one the incident
called for was `_PROMISES`, a list of promising phrases:

```python
_PROMISES = (
    "để anh",
    "để em",
    "để tôi",
    "để mình",
    "anh sẽ",
    "em sẽ",
    "tôi sẽ",
    "mình sẽ",
    "i'll",
    "i will",
    "let me",
    "we'll",
    "we will",
)
```

It was drawn from one incident, recorded in `Responder.draft`'s docstring:
*"ok có correlationId rồi, để anh trace thử"*. The list catches that sentence.

**It does not catch the promise.** Three `ask_for_details` rows in
`data/friday.db` — every one this system has ever sent — went out under the
operator's name ending in a commitment the list has no entry for:

| id | text |
|----|------|
| 2 | …Không có thì em gửi anh cái curl em đang gọi cũng được, **anh trace giúp**. |
| 3 | em gửi anh cái correlationId hoặc curl em gọi đi, **a trace giúp** |
| 4 | …với nhé, **anh trace giúp**. correlationId nằm trong response header `x-request-id` đó em. |

Run against the live predicate, all three return `None` — no objection — while
the sentence from the docstring is still refused:

```
None <- correlationId nằm trong response header `x-request-id` đó em…
None <- em gửi anh cái correlationId hoặc curl em gọi đi, a trace giúp
None <- em gửi anh cái correlationId hoặc curl em đang gọi với nhé…

control: "it promises something ('để anh')"
```

`check.py` landed 2026-09-06 (`05b4c51`); the three were sent 2026-09-14. The
floor was in place and passed them.

This is not a missing entry. It is the wrong shape of rule: a blacklist of
phrasings against a language that has unboundedly many. `để anh trace`,
`anh sẽ trace`, `anh trace giúp`, `anh trace hộ`, `a lo`, `anh xem cho` are one
promise in six spellings, and the list can only ever hold the ones somebody has
already been burned by.

All six are refused as of this ticket — but only after review, and that is
recorded below rather than smoothed over: the first version of the fix caught
four of them and left out the two whose verbs looked too ordinary to name.

## What replaces it

Not a longer list. A rule in the other direction, and it is the one `_KEPT`
already states from the other side.

`_KEPT` says: **the draft must still name what the template named.** Its mirror
is: **the draft may not name work the template did not.** The question this
system asks is always for a *thing* — "the correlationId, or the curl you
used", "which environment you're on", "what access you need". No question it
can ask names an action. So an action verb in the draft is content the model
added, and adding content is exactly what `config.yaml`'s justification for
skipping approval says cannot happen: *"what is being asked never changes, only
the wording does."*

One closed list of work verbs, one containment test, symmetric with `_KEPT`.
`_PROMISES` stays — it catches `để anh lo` and `I'll` without a listed verb —
but it is no longer the rule carrying this.

**This refuses more than promises, and that is the point rather than a side
effect.** Row 2 also teaches the reporter where to look — *"em check trong
Postman tab Headers hoặc DevTools Network là thấy"* — which is not a promise
and is still content nobody approved, written by a model from channel text. It
fails the new rule on `check`, correctly.

The trade is the one ticket 05 already made and this ticket does not reopen: a
false refusal sends a plainer question; a false acceptance sends the operator's
colleagues something the operator did not say.

## Acceptance criteria

- [ ] The three rows above are refused, driven from their literal text
- [ ] The incident in `Responder.draft`'s docstring is still refused
- [ ] An ordinary ask naming no work still passes — the responder is not
      made pointless by this
- [ ] Matched as words, not substrings: `checklist` is not `check`
- [ ] Folded the way every other rule here is, so a decomposed accent is not
      an evasion
- [ ] The whole suite passes
- [ ] The new rule is deleted once and watched go red
- [ ] `CLAUDE.md` and `config.yaml` say how many rules there are and what the
      new one is, in the same commit

## Six existing tests change, and none of them is this rule being made to pass

The ticket predicted one and there were three. All three share one draft
string, `"anh check giúp em cái server nhé"`, written before `_WORK` existed
and incidental to what each is testing:

- `test_the_reason_a_question_is_asked_is_not_part_of_the_question` — subject
  is the `_REASON` strip, that a draft is not made to recite the parenthetical
- `test_a_bracket_inside_the_models_reason_does_not_re_arm_the_bug` — subject
  is that the strip is positional rather than a balanced pair
- `tests/test_pool.py::test_ask_clarification_reaches_the_reporter_in_the_responders_words`
  — subject is that the reporter sees the responder's sentence, not the
  template; the stub returned that string

Each keeps its subject and its assertions; only the draft text moves, to
`"em đang chạy trên môi trường nào thế?"` — a question naming no work, and
naming none of `_KEPT`'s words either, so the strip is still what decides the
first two. Recorded here rather than edited quietly.

Adding `xem` to `_WORK` after review moved three more, all of them drafts
written to exercise a different rule:

- `test_a_draft_carrying_a_link_is_refused` and
  `test_a_link_in_capitals_is_still_a_link` — subject is the link rule; both
  opened with "xem ở https://…"
- `test_a_template_with_nothing_untranslatable_binds_only_the_other_rules` —
  subject is that the other rules still bite when the kept-words rule says
  nothing; its draft said "xem https://x/y"

Each dropped the word and kept everything else. They failed *loudly* — every
one asserts the reason names `link` — so the shadowing was visible rather than
silent, which is the only reason this was safe to do.

That six tests had to move is itself the finding, and it cuts both ways.
`check` and `xem` were the ordinary words to reach for when writing a
plausible Vietnamese draft, which is exactly why the model reached for them —
and exactly why naming them will refuse real drafts too. That is the trade
ticket 05 made and this ticket keeps.

## What the review changed

**One hard finding, and it was the rule being switchable off by the untrusted
half.** The work list was read against the whole `asking`. `_question_from_
clarify` (`friday/dag/prepare.py:415`) appends `clarify.because` — free text an
extractor model wrote from channel content — so a model could exempt its own
draft by writing `trace` into `because`. Measured by the reviewer:

```
plain template      : it names work the question did not (trace)
because says 'trace': None          <- the real row goes out unread
```

That is the shape this whole file exists to close, one level up: the single
path with no human in it, with its floor removable by the half nobody trusts.
The neighbouring `_KEPT` rule already strips the reason with `_REASON`, for a
softer reason than this one has. Stripped here too. Everything the strip leaves
is code — `asked_as` phrases from `friday/domain/models.py`, `_RULES` messages
from `friday/domain/validation.py` — because both builders put every
model-written word inside the bracket.

The three tests pinned `asking=TEMPLATE`, which carries no reason clause, so
nothing here would have caught it.

**And the exemption was read for letters while the rule was read for words.**
`word not in wanted` against `_says(said, word)` — a template containing
`checklist` disabled `check`. Both sides are `_says` now.

**A multi-word entry could be walked past by a space.** `re.escape` turns the
space in `kiểm tra` into a literal one, so `kiểm  tra` or a line break between
them matched nothing. Any run of whitespace now.

**Two findings deliberately not acted on.** `_says` compiles a pattern per
call — measured at 23.7 µs per `rejected()`, on a path already behind a model
call, and the docstring's reason for not precompiling (the list stays a list of
words) is the real one. And a draft that both names work *and* drops every
kept word now reports only the work reason: refused either way, so a line in
the log, not a hole.

**The spec axis found the rule was still a blacklist, of lemmas rather than of
sentences, and proved it by conjugating.** `anh tracing giúp` passed: `trace`
was on the list and `tracing` was not, so the one verb this system has already
been burned by was unreadable to the rule written for it. `_INFLECTION` drops
a trailing `e` and allows `e|es|ed|s|ing`, so `trace` reaches `tracing` and
`handle` reaches `handling`. Doubled consonants are still missed —
`debugging` is not `debuging` — and that is written down rather than claimed
closed.

**And two of the six spellings this ticket's own Why rests on still passed.**
`a lo` and `anh xem cho`: `lo` and `xem` had been left out as "too ordinary to
carry the meaning". An argument whose own examples walk past the fix is not an
argument, and leaving them out optimised the cheap side of a trade this file
states the other way round. Both are named now. `làm` and `test` are still
out, for a reason that survives being written down: they would refuse far more
than they caught.

**Two findings were already closed by the standards axis**, which ran in
parallel and reported first: the template side reading letters where the draft
side read words, and the suite count.

**One pre-existing weak test, found and not fixed.**
`test_a_bracket_inside_the_models_reason_does_not_re_arm_the_bug` passes under
a naive balanced-pair strip, so it does not actually pin the positional strip
it names. Verified to have been true before this diff as well. Not this
ticket's, and not edited quietly either.

1226 tests pass (1213 before, +13). Six guards, each deleted once and watched
go red — the rule itself, the three the standards axis added, and the two the
spec axis did.

## Still open

**This is a heuristic and the ticket does not claim otherwise.** `_WORK` is
still a blacklist — of lemmas rather than of sentences, which is a much
smaller and much better-covered list, and still not a closure. Found by the
review and still passing:

- `"anh làm ngay"` — `làm` is every second sentence in this language and
  naming it would refuse far more than it caught
- `"mai có kết quả cho em"` — a commitment with no verb of work in it at all,
  which no list of verbs can reach
- `debugging`, `debugged` — doubled consonants are outside `_INFLECTION`

The rule is narrower than "detect a commitment" and much wider than "detect
this sentence". That is an improvement and not a proof.

**The structural answer was taken, and by the operator rather than by this
ticket.** `auto_ask_for_details` is `false` in `config.yaml` as of
2026-09-15 — a separate change, a separate commit, and a separate decision,
recorded here because a ticket whose "Still open" recommends something the
repo has already done reads as though nobody did it.
