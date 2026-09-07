# 01: Design tokens and primitives

**What to build:**

`web/src/tokens.css` defines the token system (D3, D4): colors, space,
font scale, radius, shadow, motion durations, easings. Tokens match the
operator-confirmed dark palette in spec D4.

`web/src/ui/` holds the primitive components, each consuming tokens only:
`Button`, `IconButton`, `Input`, `Dialog`, `Card`, `Pill`, `Tag`, `Skeleton`,
`Spinner`, `Toast`. Each primitive carries one responsibility, one
variant set, and one place to fix a11y or contrast later.

A grep test (`tests/test_web_tokens.py`) fails the build if any screen
uses `style={{` literals or hex outside `tokens.css`. Same test reads
the tokens file and asserts the operator's chosen palette is in it —
guarding against accidental palette swaps.

**Blocked by:** None.

**Decisions:** D3, D4.

**Status:** ready-for-agent
