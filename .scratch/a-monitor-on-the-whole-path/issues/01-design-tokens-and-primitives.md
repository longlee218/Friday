# 01: Design tokens and primitives

**What to build:**

`web/src/index.css` defines the token system (spec D3, D4): colour,
space, font scale, radius, shadow, motion durations, easings.
Tokens match the operator-confirmed dark palette in spec D4.

`web/src/ui/` holds the primitive components, each consuming
tokens only: `Button`, `IconButton`, `Input`, `Dialog`, `Card`,
`Pill`, `Tag`, `Skeleton`, `Spinner`, `Toast`. Each primitive
carries one responsibility, one variant set, and one place to fix
a11y or contrast later.

A grep test (`tests/test_web_tokens.py`) fails the build if any
screen uses `style={{` literals or hex outside `index.css`. Same
test reads the tokens file and asserts the operator's chosen
palette is in it — guarding against accidental palette swaps.

**Blocked by:** None.

**Decisions:** D3, D4.

**Status:** ready-for-agent

- [ ] `:root` declares the operator-confirmed dark palette:
      `--bg-0`, `--bg-1`, `--bg-2`, `--line`, `--ink`, `--ink-faint`,
      `--ink-mute`, `--accent`, `--good`, `--warn`, `--bad`.
- [ ] Motion tokens (`--d-fast`, `--d-med`, `--d-slow`) and
      `--ease` are declared. `prefers-reduced-motion: reduce`
      collapses every duration to 0.
- [ ] One file per primitive under `web/src/ui/` (Button,
      IconButton, Input, Dialog, Card, Pill, Tag, Skeleton,
      Spinner, Toast, plus `index.ts` re-exporting the lot).
      No primitive is declared inline in a screen.
- [ ] `tests/test_web_tokens.py` guards:
      - no `style={{ ... }}` literals in any `*.tsx` under
        `web/src/screens/**` or `web/src/ui/**`;
      - no hex literals outside `index.css`;
      - the operator palette is in `:root`;
      - motion tokens are declared;
      - light-mode media query is absent (operator chose dark-only);
      - `ui/index.ts` exports every primitive;
      - one file per primitive exists.
- [ ] Each guard mutation-tested: dropping the reduced-motion
      block, removing a primitive from `index.ts`, or
      re-introducing a `Loading…` literal flips its guard red.

Ticket also bundles the operator's call on 2026-09-08 to drop
the four tool-description sections from the prompt builders
(`search_skills_system`, `describe_skill_system`,
`read_skill_file_system`, plus the `fetch_skill` mention) and
reformat the catalogue as DeerFlow-style bare `<skill>` blocks.
That refactor is documented inline in the prompt modules.

Ticket 11 (Rooms markers) and 12 (Flow state) were originally
folded into this commit but split out as their own tickets
after the operator asked for those specific changes mid-flight.
