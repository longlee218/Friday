# 41: A stranger changes the pronouns, and nothing else

**What to build:** Writing to somebody the operator has no history with, the
agent changes how it addresses them — and changes nothing else about how it
writes.

**Blocked by:** 40 (the room decides the register)

**Status:** ready-for-agent

## Why this is the dangerous case

Learning a voice from real messages fails exactly where the cost is highest. A
new stakeholder has no history, so there are no examples, so the model falls
back to the average — and the average of the operator's messages is how they
write to their own team. The result is over-familiarity with the one person it
should not be used on.

Falling back to *the average* is the wrong default. Falling back to *the safer
register* is the same rule this system already uses everywhere else: when it is
not sure, it does not guess in the direction that costs more.

## Only the pronouns

Decided by the operator, and the restraint is the point.

Short and direct is a **personality**, not a degree of familiarity. Making a
message longer, adding a greeting, softening an uncertainty — these do not read
as more polite to somebody who does not know you. They read as stiff, and they
stop sounding like the person whose name is on the account.

So: the form of address changes. Length, structure, the absence of preamble,
keeping technical words in English, and saying plainly what is not known all
stay exactly as they are.

## Where it is written

The Responder family's section of `PERSONA.md`, beside the description of how
the operator writes — because it is a fact about who the agent is, and because
it has to apply in a channel that has no context file yet, which is precisely
when nothing is known about anybody.

## Acceptance criteria

- [ ] With no history and no written register for a person, the form of address
      is the safer one
- [ ] Nothing else about the message changes — a test compares a draft to the
      same draft for a known counterpart and asserts only the address differs
- [ ] A written register for that person or that room wins over this
- [ ] It applies in a channel with no context file at all
- [ ] The rule is written in the Responder family's section and reaches no
      other family
