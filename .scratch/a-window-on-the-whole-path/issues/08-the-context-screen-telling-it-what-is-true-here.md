# 08: The context screen — telling it what is true here

**What to build:** The one screen that writes. A channel's `overrides`, edited
as key/value pairs, with a reload that makes the change live.

**Blocked by:** 03, 05

**Decisions:** D7, D8, D9

**Status:** done

## Why

The operator's second request — "a place for me to load knowledge in". Of the
five doors knowledge currently enters through, this is the one that was built
for a person and has never been used:

| Door | Written today by |
| --- | --- |
| Skills (`skills/`) | a human editing a file, live at next restart |
| **Channel `overrides`** | **a CLI script, once, then hand-edited** |
| Agent memory | the agent, through its own tools |
| Few-shot examples | `config.yaml` plus an emoji reaction in Discord |
| Sensitive words | `config.yaml` plus a restart |

`context/` is empty. Not "sparse" — empty. No channel has a file, so the base
layer and every override are unwritten, and the mechanism designed for exactly
this request has never run in anger.

The others stay shut, deliberately (spec, Out of scope). A skill is code and
deserves a git diff. `base.yaml` reaches every channel. Agent memory is the
agent's, decided in ticket 09 of `nothing-runs-unmeasured` after argument.
Few-shot examples already have a door — the ✅ reaction — that is better than a
form because it is where the operator is already looking.

**What the screen must make obvious is layering.** `merged()` is `{**base,
**derived, **overrides}`. An operator typing `summary` into overrides is
silently shadowing whatever the summariser writes, forever. That is a
legitimate thing to want and a terrible thing to do by accident.

## Acceptance criteria

- [x] A channel with no file can be created from the page; the
      `FileExistsError` guard in `ContextStore.init_channel` still holds and
      reads as a real message, not a 500
- [x] `overrides` edits as key/value pairs — never a raw YAML textarea (D9), so
      "this channel's file is malformed" is a state the UI cannot produce
- [x] The three layers are visible as layers: what `base` says, what the
      machine derived, what the operator is overriding, and what the model will
      actually read
- [x] Overriding a key the machine writes is allowed and **flagged** — the
      summariser will keep recomputing a value nobody will ever see again
- [x] The reload is a visible action with a visible result, and it re-reads
      *every* file so a hand-edit is picked up by the same button (D8). One
      rule for when an edit takes effect
- [x] Values are stored plain — escaping happens once, on the way into a
      prompt. A test in ticket 03 pins this; the UI must not pre-escape
- [x] It is obvious that a saved change does nothing until reloaded, and
      obvious once it has
- [x] Built through the `ui-ux-pro-max` skill: visible labels not
      placeholder-only, errors next to the field, 44×44px touch targets

## Notes

This is the screen that reverses `docs/SPEC.md`'s "any interaction on the web
page" out-of-scope. Ticket 03 carries that argument; this ticket carries the
consequence, which is that a person can now change what an agent believes about
a room from a browser. Two things follow:

- The page should say which channel it is editing loudly enough that nobody
  edits the wrong room's context. A channel id is not a name a person
  recognises at a glance.
- The reload affects every agent in the process immediately. There is no
  staging, no preview against a real prompt, and no undo beyond editing back.
  A "what the model will read" panel is the closest thing to a preview and is
  why it is an acceptance criterion rather than a nicety.

Nothing else on this screen writes. Skills, memory, examples and config stay
where they are.
