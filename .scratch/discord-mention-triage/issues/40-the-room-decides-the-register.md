# 40: The room decides the register

**What to build:** The agent writes differently in different channels, because
the operator does. A customer-facing room is not the team room, and the same
colleague is addressed differently in each. Where a particular person needs
handling of their own, that is written down beside the room's default.

**Blocked by:** 39 (each family gets its own prompt)

**Status:** ready-for-agent

## Why this is not already true

The channel context has been built for some time: a store, a file per channel,
three layers with a documented precedence, and a rebuilder that writes the
learned layer on a schedule. **Nothing reads any of it into a prompt.** The
store is constructed at startup and handed only to the thing that *writes* it.

Meanwhile the responder learns its voice from the operator's eight most recent
messages **from anywhere** — no filter by room, none by counterpart. So it
learns one average voice and uses it on everybody. Written to a stakeholder,
the average of how you write to your own team is not a small error.

This is the fourth mechanism found built and unwired. The pattern is that each
half works, so nothing fails; the feature is just quietly not there.

## Room first, person as the exception

Most differences in register follow the room rather than the individual, the
room is visible and stable, and a file per channel already exists. A person who
needs something different is an exception, written where the rule it breaks
lives — so reading one file tells you how to write in that room.

A separate per-person store would force a merge rule to be invented: their file
says informal, the room says formal, and there is no correct answer to which
wins. Better not to create the question.

## Loading

At startup, held in memory, like every other thing the operator writes —
configuration, the persona, skills, promoted notes, hand-written examples.
Making this one different creates two rules for *when does my edit take
effect*.

Reading per call is not an option: it is blocking file I/O in the loop that
reads the gateway.

The learned layer is not written by the operator, though. The rebuilder runs
in-process on its own cadence, so when it writes that layer it updates the copy
in memory too. No file is read twice and nothing waits for a restart.

## Acceptance criteria

- [ ] The responder and the node that composes a reply receive the channel's
      base, learned and override layers, in the documented precedence
- [ ] No other agent receives them
- [ ] A per-person entry in the override layer changes how that person is
      addressed, and only that person
- [ ] A channel with no file behaves exactly as today
- [ ] The learned layer becomes visible without a restart when the rebuilder
      writes it
- [ ] Nothing reads a context file per message
- [ ] The layered content is escaped at the same seam as everything else that
      reaches a prompt — the base and override layers are operator-written, the
      learned layer is not
- [ ] A test drafts the same message in two channels with different registers
      and asserts they differ
