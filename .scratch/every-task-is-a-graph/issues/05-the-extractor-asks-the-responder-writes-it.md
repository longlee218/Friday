# 05: The extractor asks, the Responder writes it

**What to build:** When something is missing, the agent that just read the
whole thread says *what* is missing and why; the agent that knows the
operator's voice says it in a sentence a person would write. The reporter is
asked once, in the room's register, and code still refuses a malformed value
the model let through.

**Blocked by:** 04

**Decisions:** D11, D12

**Status:** done

## Why

Two capabilities are being kept apart on purpose. The extractor has just read
every message the reporter sent and is the only thing in the system that knows
what it could not find. The Responder knows how to speak to this person, in
this room, in the operator's voice, with the pronouns a stranger gets and the
skills that explain a term — tickets 36, 40 and 41, all of which must keep
applying to this sentence.

So the extractor carries the tool and the Responder fulfils it. The tool
carries *intent*, never words: `fields` is a closed enum of that type's own
fields, so a model cannot ask for something that does not exist, and cannot
invent phrasing that bypasses the voice.

Code stays the floor. The rules run afterwards regardless of what the model
did, because a rule that runs after the model can still be argued out of by a
persuasive message — and one that runs anyway cannot.

## Acceptance criteria

- [x] The extractor holds `ask_clarification(fields, because)`, with `fields`
      a closed enum of the task type's own fields
- [x] A call becomes an ask whose sentence the Responder writes, with the
      channel's register, the stranger rule and the skills all still applied
- [x] The Responder does not hold the tool; it fulfils it
- [x] Validation runs afterwards regardless: a malformed correlationId the
      model did not ask about is refused by code and asked for with the
      template
- [x] Both paths produce the same action, and the existing per-task ask cap
      and dedup apply equally to both
- [x] A Responder that is off or fails still falls back to the template rather
      than producing nothing
- [x] Driven at the scripted-model seam: force the tool call, assert the
      sentence came from the Responder and not from the extractor
- [x] Exempt from the byte-identity rule — D11 and D12 change behaviour

## What it came to

**The tool.** `friday/extraction/__init__.py` gains `Clarify(fields, because)`
and `_clarify_tool(params_cls)` — one function building `ask_clarification`
for any type, `fields` a `Literal` closed to that type's own dataclass fields
minus `MODEL_AUTHORED` (`summary`, promoted from `_workflows`-private to
public since extraction now reads it too). `Extractor.run` gained a
`_Capture` (same pattern as triage's), and its return type changed from
`Params | None` to `(Params | None, Clarify | None)` — whether the model
asked is independent of whether it also wrote usable field text.

**A real bug found and fixed while building it.** `from __future__ import
annotations` stringifies every annotation in the module, including
`fields: list[FieldName]` inside the closure that builds the tool per type —
and `FieldName` is a *local* variable, invisible to `get_type_hints`'s
eval against the module's globals. Setting
`ask_clarification.__annotations__["fields"] = list[FieldName]` directly,
while `FieldName` is still in scope, bypasses the string-eval path for that
one parameter. Caught immediately by the suite (`NameError`), not by a test
written for it — worth knowing for the next per-type dynamic tool.

**Where the intent goes.** `friday.workflows.prepare` (node 0 of every
graph) receives `Clarify` from `extract()` and decides, in order: a
structural/semantic problem always wins (code is the floor); otherwise, if
the model asked about fields that are *still* blank after filling, that
becomes the Ask; a Clarify naming only already-filled fields is silently
dropped (nothing left to ask about). `_question_from_clarify` renders the
same shape `_question` already renders `Problem`s — content for the
Responder to write from — so `WorkflowRunner._say`/`Responder.draft` needed
**zero changes**: they already treat `asking=` as material to compose from,
not a sentence to lightly reword (the Responder's own instructions already
say "you are shown... what needs to be said. Write that message the way
they would write it"). This is what makes "the Responder does not hold the
tool; it fulfils it" true without a new Responder method — fulfilling means
writing from whatever `asking=` was actually being asked, indifferent to
whether that content came from a code template or a model's own judgement.

**The extraction prompt changed** (this ticket is exempt from byte-identity):
one paragraph telling the model the tool exists and when to reach for it,
placed before the existing "reply in JSON only" instruction so both can be
followed together.

**Tests.** `test_extraction.py`: a scripted-model test forcing the tool call
and asserting the `Clarify` it produces; a schema test pinning the enum
against every type's own fields minus `summary`. `test_workflow_api_issue.py`:
three unit tests on `prepare()`'s ordering, each mutation-tested — code wins
over a Clarify naming a different field, an already-filled field is not
re-asked, and a Clarify becomes the Ask once code has nothing to say.
`test_workflow_runner.py`: one full pool-seam test — a traceable report (so
the structural floor is silent), the extractor asks about `environment`, and
the reporter must see the Responder's fixed stub text, not the rendered
template. Mutation-tested by skipping `_say`'s Responder call: the fallback
text that leaks through is `_question_from_clarify`'s, not the structural
`_question`'s — confirming the test exercises the Clarify path specifically,
not just "some Ask reached the outbox."

617 tests, up from 611 (six new). `friday/extraction/prompt.py` is the only
prompt file touched, deliberately, per the exemption; the responder's
prompt module is untouched.
