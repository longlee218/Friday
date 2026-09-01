# 41: A stranger changes the pronouns, and nothing else

**What to build:** Writing to somebody the operator has no history with, the
agent changes how it addresses them — and changes nothing else about how it
writes.

**Blocked by:** 40 (the room decides the register)

**Status:** done

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

- [x] With no history and no written register for a person, the form of address
      is the safer one
- [ ] ~~Nothing else about the message changes — a test compares a draft to the
      same draft for a known counterpart and asserts only the address differs~~ —
      **not testable with a scripted model**: what changes in the *message* is
      the model's doing. What is tested is the input: exactly one line differs
      between a stranger's prompt and a known person's, and the persona says
      what that line may change. Verify on the live provider.
- [x] A written register for that person or that room wins over this
- [x] It applies in a channel with no context file at all
- [x] The rule is written in the Responder family's section and reaches no
      other family

## What it came to

"Known" is: the operator and this person have replied to each other, in either
direction, ever — or the operator wrote them into the room's `people:`. Not
"both have spoken in this channel": the operator has spoken in every watched
channel, which would make everybody known.

When neither holds, the responder's prompt gains one line — `You have not
written to this person before` — and the Responder section of `PERSONA.md` says
what that line is allowed to change: the form of address, and nothing else.

**The pronouns in `PERSONA.md` are a guess** — *anh/chị* for them, *mình* for
the agent. The operator decided that only the form of address changes; they did
not say to what. Correct the two words there if they are wrong; nothing else
depends on them.

