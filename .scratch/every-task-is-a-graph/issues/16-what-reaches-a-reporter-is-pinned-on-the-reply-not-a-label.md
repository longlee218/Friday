# 16: What reaches a reporter is pinned on the reply, not on a label

**What to build:** The rule that only the agent writing to the reporter may
produce the message they read is enforced against the message itself, not
against a persona label. Once it is, the label and the file behind it go, and
each agent's voice is written where that agent is defined.

**Blocked by:** 15

**Decisions:** reverses CLAUDE.md's *"One persona, two families, and the family
is decided in code"*

**Status:** ready-for-agent

## Why

**The label's test did not catch the bug the label exists to prevent.** Ticket
10 was a composing node with no agent configured, returning the analysis's own
prose and a raw diff straight to the reporter. The invariant test is written in
terms of the persona family, and it passed the whole time — there was no
mis-assigned family to find, because there was no agent at all. Catching it
took a second, behavioural test written afterwards.

So the anchor is wrong. What matters is not which label an agent carries but
**what can construct the message a reporter reads**. Today that is two things:
the tool the composing node calls to answer, and the responder. Anchored there,
ticket 10's bug is caught by construction rather than by a test somebody
thought to add later.

**Once the anchor moves, the label stops earning its place.** The persona file
has two consumers and distinguishes two sections. Its job was to keep one
voice in one place — but the operator's own rule from the ticket-15
conversation applies: two agents with different purposes will diverge in prompt
whatever we do, and holding their voice in one shared section postpones that
rather than preventing it. With every agent's prompt assembled in code from the
shared builders, the voice belongs in the prompt of the agent that uses it.

**The vocabulary entry is already stale, which is its own evidence.** CONTEXT.md
still describes Persona as *"Three modes, chosen per agent"* — the
configuration knob that was deleted and replaced by two families. A definition
nobody noticed had gone wrong is a definition nothing depends on.

**What is genuinely lost, and it is a real cost:** the operator can currently
change how the system sounds by editing a prose file and restarting, without
touching Python. Afterwards, changing the voice is a code change. For a system
with one operator who also owns the repository this is small, but it is a loss
and this ticket is where it is chosen rather than discovered.

**Order matters and is not negotiable.** Re-anchor the invariant, watch the new
test catch ticket 10's shape, and only then delete anything. Deleting first
leaves the one rule in this system that protects an outside reader with no test
behind it, however briefly.

## Acceptance criteria

- [ ] The invariant is enforced on what can construct the message a reporter
      reads, and the test is watched go red against the shape ticket 10 shipped
      — before anything is deleted
- [ ] Each agent that speaks in the operator's voice carries that voice in its
      own prompt, assembled through the shared section builders
- [ ] The persona family, the persona reader, the persona file and the
      configuration knob that points at it are gone, and nothing imports them
- [ ] No agent that was getting a persona section loses text it needs, and no
      agent that was deliberately getting none — triage, the extractors —
      starts getting any
- [ ] Prompts are **not** byte-identical here, and the diff is reviewed
      deliberately: this ticket moves text between modules by design, so the
      before/after capture is read rather than asserted equal
- [ ] CLAUDE.md records the reversal in the same commit, with the reason: the
      family-anchored test could not catch the bug the family exists to prevent
- [ ] CONTEXT.md's Persona entry goes, and whatever replaces it describes what
      is actually enforced
