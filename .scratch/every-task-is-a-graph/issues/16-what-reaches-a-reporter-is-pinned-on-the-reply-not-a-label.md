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

## What the review caught

**A fourth spelling routed around the anchor.** The guard resolved imports to
catch `Reply(x)`, `actions.Reply(x)` and an aliased `R(x)` — three, found by
testing each. `from friday.domain.actions import *` then `Reply(x)` was the
fourth and walked straight past.

Worth recording how nearly it was missed twice. I re-ran the reviewer's
mutation and it went red, so I judged the finding wrong. It went red for the
wrong reason: I appended the star import to `graph.py`, which already imports
`Reply` directly, so the name was bound either way. Repeating it in a module
that had never imported `Reply` reproduced the hole exactly as reported. A
mutation that passes for a reason you did not check is not a test of anything.

**Coverage lost in the rewrite.** The test this replaced looped over every
node asserting each non-composer carried the step's voice; the rewrite checked
`analyze_stack` alone. So a node pasted with the operator's voice would have
shipped — the precise shape of the bug the invariant exists to stop. It loops
over `NODES` again, mutation-tested by giving `find_code_path` the operator's
voice.

**Five docstrings still described the deleted concept in the present tense**,
in the modules this ticket rewrote — a prompt module claiming families "arrive
here as arguments", a builder claiming "which persona is stated once", the
pool describing an agent "wearing the Responder persona". CLAUDE.md's own
warning is that a reversed decision goes stale silently; it was corrected and
five smaller sites were not.

**Two comments asserted something nothing checks** — that the two copies of
the voice are the same text. The duplication is deliberate and an equality
test would contradict the reason for it, so the comments now say where the
other copy *is* and claim nothing about its contents. An unchecked claim in a
comment is the kind that quietly stops being true.

Also: a comment describing the node texts had been left sitting above the
voice constants, and CONTEXT.md pointed at a `## Reply` section that does not
exist.

## What the second review caught

Both reviews independently found the coverage regression, which is worth
noting on its own: two readers looking from different angles landed on the
same missing loop.

**The anchor was one hop away from the thing that matters.** What a reporter
actually reads is a `Kind.REPLY` outbox row, queued by `Pool._propose` from a
plain string. `Reply` construction is pinned, and `Reply` happens to be
`_propose`'s only caller — so the anchor covered the real boundary *by
coincidence of there being one caller*. A second `_propose(task, whatever)`
would put text in front of a reporter with no `Reply` constructed anywhere,
and the guard would not blink.

Pinned directly now: `Kind.REPLY` is queued in exactly one place.
Mutation-tested by turning the help-wanted row into a reply row.

**A third evasion, and two that stay open on purpose.** The fully-qualified
`friday.domain.actions.Reply(x)` is a chain of attributes rather than a name,
and walked past — plausible enough to close, and closed. `getattr`, a table of
constructors, and `dataclasses.replace` on an existing `Reply` cannot be
followed by reading source, and the test now says so rather than leaving a
reader to assume it is a proof. Nothing in this codebase builds an action that
way; if something ever does, the code is what should change.

**Independent confirmation of the byte-identity claim.** The reviewer
extracted the three new constants and re-ran the old splitter over
`60166f9:PERSONA.md`: `VOICE` equals the old Responder section exactly, and
the two graph constants equal their sections plus the `"\n\n"` the old
assembly added. Old `section + "\n\n" + text + catalogue` equals new
`(section + "\n\n" + text) + catalogue`, for every node, with and without
skills.

**One behaviour did change, and it is an improvement worth knowing.** With
`PERSONA.md` missing, agents used to run voiceless by design — `load` returned
an empty `Persona` and every section rendered empty. The voice is
unconditional now. There is no longer a way to start the system with agents
that have no idea who they are, which is the right default, but it is a
default that used to be a fallback.
