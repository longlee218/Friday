# Notes from the design audit

13 problems the `ui-ux-pro-max` audit found in the operator's draft of
8 tickets, classified by severity. The ticket numbers refer to where
each is fixed; this file is the audit trail, not the spec.

## Critical (5) — accessibility and performance

1. **P1 — Live feed needs `aria-live="polite"`** (tickets 04, 08).
   Dynamic content not announced to screen readers; defeats the
   "monitor" name.

2. **P1 — Color-only state indicators on task cards** (ticket 04).
   `● extracting` is decorative; the card must carry a text label too.

3. **P2 — Touch target rule ignored** (ticket 04). Operator said
   desktop-only so row height drops to 28-32px but `pause on hover`
   replaced with `pause on tap` and a visible "PAUSED" affordance —
   desktop pointer hover does work, the tap phrasing keeps mobile
   alive as a fallback.

4. **P3 — SSE without virtualization** (ticket 05). 100 events/min
   re-rendering the whole feed kills the page. Tickets 05 and 04 add
   `React.memo` and a virtualized list for > 200 rows.

5. **P3 — Skeleton shape ≠ content shape** (ticket 03). Skeleton must
   use the same `--min-height` as the rendered content, or the page
   jumps when the data arrives (CLS).

## Medium (5) — applied during the relevant ticket

6. **P4 — Fira Code as body font is wrong.** Code mono for headings
   hurts eyes on long titles. Body and heading are Fira Sans; Fira
   Code only for `id`, latency, token counts, and table data (ticket
   01).

7. **P7 — "Exit-faster-than-enter" missing.** Enter 240ms, exit 180ms;
   enter 200ms, exit 140ms. Added to the motion system in ticket 02.

8. **P8 — Toast errors lack action button.** A toast that says
   "Rename failed" without a Retry button forces the operator to find
   the form again. Toast component in ticket 03 supports `action:
   {label, onClick}`.

9. **P5 — Mockup has no breakpoint at all.** Operator said desktop
   only, so the design specifies one breakpoint at 1280px and below
   that the two columns collapse to a tab switcher — ticket 04.

10. **P9 — Breadcrumb unbounded depth.** Task has many calls, each
    call has many tools. Breadcrumb collapses anything beyond 4
    levels into `...`. Ticket 06.

## Minor (3) — noted for later boards

11. **P10 — If charts ever land, time series are line, not bar.**

12. **P6 — `tracking` not in the scale.** Inter at ≥ 22px gets
    `tracking: -0.02em`; smaller sizes default to 0. (Future ticket
    when the scale expands.)

13. **P6 — Dark mode not decided by MASTER.** The audit caught this
    too — the Master said "anti-pattern: light mode" but shipped a
    light palette. Tokens override. (Closed in D4.)

## What the audit also taught

- The Master file is helpful as a vocabulary, harmful as a literal
  source of truth. It is the result of a query, not a constraint.
- A serious/critical violation rate that a code review would miss is
  roughly 1 per 200 lines of new UI. Three screens → three review
  passes → three chances to ship a regression. Tickets 08 and 09
  replace review with a gate.

This file is not load-bearing. It is the audit trail that justifies
tickets 08 and 09 existing, so a future reader does not think they
were added for fun.
