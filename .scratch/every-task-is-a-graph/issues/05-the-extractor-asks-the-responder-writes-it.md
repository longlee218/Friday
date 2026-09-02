# 05: The extractor asks, the Responder writes it

**What to build:** When something is missing, the agent that just read the
whole thread says *what* is missing and why; the agent that knows the
operator's voice says it in a sentence a person would write. The reporter is
asked once, in the room's register, and code still refuses a malformed value
the model let through.

**Blocked by:** 04

**Decisions:** D11, D12

**Status:** ready-for-agent

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

- [ ] The extractor holds `ask_clarification(fields, because)`, with `fields`
      a closed enum of the task type's own fields
- [ ] A call becomes an ask whose sentence the Responder writes, with the
      channel's register, the stranger rule and the skills all still applied
- [ ] The Responder does not hold the tool; it fulfils it
- [ ] Validation runs afterwards regardless: a malformed correlationId the
      model did not ask about is refused by code and asked for with the
      template
- [ ] Both paths produce the same action, and the existing per-task ask cap
      and dedup apply equally to both
- [ ] A Responder that is off or fails still falls back to the template rather
      than producing nothing
- [ ] Driven at the scripted-model seam: force the tool call, assert the
      sentence came from the Responder and not from the extractor
- [ ] Exempt from the byte-identity rule — D11 and D12 change behaviour
