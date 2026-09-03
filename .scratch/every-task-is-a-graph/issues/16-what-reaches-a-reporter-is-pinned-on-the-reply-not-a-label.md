# 16: What reaches a reporter is pinned on the reply, not on a label

**What to build:** The rule that only the agent writing to the reporter may
produce the message they read is enforced against the message itself, not
against a persona label. Once it is, the label and the file behind it go, and
each agent's voice is written where that agent is defined.

**Blocked by:** 15

**Decisions:** reverses CLAUDE.md's *"One persona, two families, and the family
is decided in code"*

**Status:** done

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

- [x] The invariant is enforced on what can construct the message a reporter
      reads, and the test is watched go red against the shape ticket 10 shipped
      — before anything is deleted
- [x] Each agent that speaks in the operator's voice carries that voice in its
      own prompt, assembled through the shared section builders
- [x] The persona family, the persona reader, the persona file and the
      configuration knob that points at it are gone, and nothing imports them
- [x] No agent that was getting a persona section loses text it needs, and no
      agent that was deliberately getting none — triage, the extractors —
      starts getting any
- [x] Prompts are **not** byte-identical here, and the diff is reviewed
      deliberately: this ticket moves text between modules by design, so the
      before/after capture is read rather than asserted equal
- [x] CLAUDE.md records the reversal in the same commit, with the reason: the
      family-anchored test could not catch the bug the family exists to prevent
- [x] CONTEXT.md's Persona entry goes, and whatever replaces it describes what
      is actually enforced

## What it came to

**The anchor moved first, and the argument was run rather than asserted.**
`test_a_reply_is_constructed_in_exactly_one_place` walks every `Reply(` in
`friday/` by `ast` and requires exactly one, the `answer` tool. Then ticket
10's shape was reintroduced — `_compose_reply`'s agentless fallback returning
`Reply(said)` — and the two anchors were run side by side:

    new anchor (Reply construction) : FAILED
    old anchor (Family label)       : passed

That is the whole ticket in one run. Only after that did anything get
deleted.

**One place constructs a `Reply`, and it was never the responder.** Worth
recording because it surprised me: the responder does not build a `Reply` at
all — it rewords an `Ask`. So "who can say something a reporter reads in the
operator's name" was always a single call site, and the label had been
standing in for a fact that was simpler than the label.

**The voice moved into the two prompts that use it.** `PERSONA.md`'s Responder
section is now a constant in `friday/responder/prompt.py` and, separately, in
`friday/dag/api_issue/prompt.py` for the composing node; the Node section is
in the graph's module for the other four. Two copies of the Responder text,
deliberately — the operator's own rule from the ticket-15 conversation, that
two agents with different jobs diverge whatever you do, and holding the text
in one place postpones that rather than preventing it.

Gone: `Family`, `Persona`, `persona.render`, the file, the `persona:` block in
`config.yaml`, the mount in `compose.yaml`, and the `persona=` argument
threaded through the responder and the node builder.

**Prompts came out byte-identical, which this ticket said they would not.**
The criterion asked for the diff to be *read* rather than asserted equal, and
reading it is what earned that: the first capture differed on every node by a
missing blank line, because `_persona()` used to return `f"{text}\n\n"` and
concatenating constants dropped the separator. Fixed, recaptured, identical.
Asserting equality would have hidden nothing here — but only because the diff
was read first.

`tests/test_persona.py` is `tests/test_agent_voice.py`, `git mv`'d. Seven of
its eleven tests were about a file loader and went with it; the four that
mattered were about *what each agent ends up being told* and are rewritten
against the prompts directly — the composer speaks in the voice, a step is
told it is not one, the responder speaks in it, and triage and the extractors
are told nothing about voice at all.

638 tests pass (644 before; six loader tests removed with the loader, one
family-anchored invariant test replaced by the Reply anchor).
