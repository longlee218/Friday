# 04: The door refuses harder now that it opens

**What to build:** `check_exposure` stops warning and starts refusing, because
the thing behind it can now be written to.

**Blocked by:** 03

**Decisions:** D10

**Status:** done

## Why

`check_exposure`'s docstring is the whole argument, and ticket 03 invalidates
half of it:

> *"The board has no authentication, and the design says that is safe because
> it is read-only. That argument was always about **writes**. Reading it hands
> over every captured message and every model prompt, and what actually made
> that safe was that it only ever answered on loopback."*

It was written for a process that could not be told anything. After ticket 03
it can: a channel's `overrides` flows into the instructions of every agent
working in that room. Binding that to a network without a credential is no
longer a disclosure risk, it is a control one — somebody who can reach it can
change what the agent believes about a room, and every reply after that carries
it.

Today the function has three outcomes: return if loopback or a token is set;
**warn and continue** if `/.dockerenv` exists; `SystemExit` otherwise. The
container branch exists for a good reason — a container's own loopback is
unreachable from outside it, so binding to `0.0.0.0` inside one means "this
container" and the real publish rule is `ports: ["127.0.0.1:8086:8086"]` one
layer out. That reasoning still holds for reading. It does not hold for
writing, because it depends on a compose file this process cannot see.

**No token on the write path itself (D10).** Loopback plus a tunnel is the
model, and anyone who can POST to this can already read `data/friday.db`. A
token on an inside door is one more variable to remember that stops nobody. The
guard belongs at the door to the outside, which is this function.

## Acceptance criteria

- [x] Binding a non-loopback address with no `BOARD_TOKEN` refuses, container
      or not — the warn-and-continue branch no longer applies once a write path
      exists
- [x] The refusal message says *why* it changed: that the process now accepts
      writes, and names what they reach (a channel's context, and through it
      every prompt in that room). A guard whose message only says "refusing" is
      one somebody works around
- [x] The container case still has a supported answer — set `BOARD_TOKEN`, or
      bind loopback and publish through compose — and the message names it,
      because a guard that leaves no correct path is one that gets deleted
- [x] Tests cover every branch: loopback, token-set, container-without-token,
      plain-without-token. Each is deleted once and watched go red

## Notes

Whether `BOARD_TOKEN` being set should also *enforce* anything on requests is
a separate question and deliberately not answered here. Today it is a
declaration that the operator has thought about exposure, not a credential
anything checks. Widening it into real authentication is the ticket that comes
with multiple users, and nobody has asked for that.

This is the one guard in this board that protects an unscoped Discord account
token from the internet. Treat the mutation test on it as the deliverable, not
as paperwork.
