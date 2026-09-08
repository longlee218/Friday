# 13: Rooms — Agent vs Reporter marker

**What to build:**

The Rooms screen today distinguishes the watched account's own
messages with a faint `(us)` text node next to the author name.
That is not enough on a long room — the operator needs to know
whether a row was sent by the agent (a reply on the operator's
behalf) or by somebody addressing them, without scanning the text
on every line.

Ticket 13 makes the distinction visible at a glance:

- A subtle background tint on the row: agent messages get a
  low-saturation accent wash, reporter messages stay on the
  resting background. The contrast is small by design — colour is
  decoration, not the only signal.
- A left-border accent on agent rows. Two pixels of `--accent`,
  the same colour the audit uses for "live" — the operator's
  eyes pick it up before they read anything.
- A label on the row header so colour-blindness and screenshots
  do not lose the signal. The audit's rule: glyph + label, never
  colour alone.

**Decision:** Agent messages are coloured `--accent` (cyan).
Reporter messages stay neutral — the contrast is *who is talking
to me*, and the operator is the only agent-coloured party.

**Blocked by:** 11.

**Decisions:** D2 (audit #2).

**Status:** ready-for-agent

- [ ] `web/src/screens/RoomsScreen.tsx` renders the agent
      message row with `is_own === true` carrying: a left border
      of `--accent` 2px wide, a low-saturation accent background
      tint (`--accent` mixed with `--bg-1` at ~10%), and a small
      label "Agent" next to the author name. The label survives
      colour-blindness; the colour is decoration.
- [ ] Reporter rows (`is_own === false`) keep the existing
      resting background and no left border.
- [ ] `tests/test_web_tokens.py` pins the agent styling: dropping
      the `is_own` check, the `--accent` border, or the "Agent"
      label flips the guard red. The label and the colour have to
      travel together — colour alone is an accessibility
      regression the build catches.
- [ ] The CSS rule is in `web/src/index.css` under a `.msg.is_own`
      selector, not inline-style — the inline-style guard
      (test_web_tokens.py) already enforces this; the new rule
      has to obey it.
