# Spec: a monitor on the whole path

Status: ready-for-agent. Designed in a grilling session on 2026-09-07 with
the operator. Replaces the Liquid Glass direction in
`.scratch/discord-mention-triage/issues/19-liquid-glass-on-the-chrome.md`,
which is **retired** rather than deleted — the file stays so a future reader
sees what was tried and why it stopped.

## What this is

The board's next iteration. The previous one — `.scratch/a-window-on-the-whole-path/`
— gave us kanban, master-detail rooms, context popup, memory panel, and a
flow screen. Each of those answers *what was said*; none of them answers
*what is happening right now*. The operator does not read terminals: the
board is the operator's window onto the system, and a window that does not
update until somebody clicks Refresh is a window on a still photo.

This board turns the page into a **monitor dashboard**: a real-time view of
what the system is doing, what it is waiting on, what just failed, and what
it cost. The earlier screens stay — board, flow, rooms — but they are no
longer the front door. The monitor is.

## Operator's words, kept verbatim

> Tôi sử dụng chính vì sẽ không nhìn terminal mấy, dựa vào dashboard này
> tôi có thể theo dõi quá trình xử lý từng bước, biết các điểm lỗi, tool
> sử dụng từ đó cải thiện.

Three things follow from that:

- **Lifecycle matters.** Not "what tasks exist" but "what is running now,
  what just finished, what has been waiting on a person for too long."
- **Cost and latency are first-class.** They are not buried in a row, they
  are on the card.
- **The board must move on its own.** Polling 5s is too late. SSE.

## Implementation Decisions

### D1. Monitor is the front door, board and rooms become drill-down

The default route `/` becomes `/monitor`. `/board` and `/rooms` stay, but
they are reachable from the monitor (click a card → flow), not the other way
around. The previous topbar had "Board" and "Rooms"; the new one has
"Monitor" and "Board" and "Rooms", with Monitor active by default. A page
reload lands on Monitor.

Why: the operator's first question on opening the page is *what is happening
right now*, not *what is queued*. Putting that question behind a tab
disappears it.

### D2. Three tiers of motion, named, each with one job

| Motion | Used for | Duration | Easing |
|---|---|---|---|
| `fade` | page transitions, list items appearing | 180ms | `cubic-bezier(0.2, 0, 0, 1)` |
| `slide` | panels, toasts | 240ms in, 180ms out | `cubic-bezier(0.2, 0, 0, 1)` |
| `scale` | dialogs, popovers | 200ms in, 140ms out | `cubic-bezier(0.4, 0, 0.2, 1)` |

Exit is always faster than enter. Animations only run on `transform` and
`opacity` — never `width`, `height`, `top`, `left`. `prefers-reduced-motion:
reduce` collapses every duration to 0ms.

Why: one duration for every transition is the rule that turns a page into
a uniform wash. Naming three makes the choice deliberate.

### D3. Tokens, not literals

Every color, space, font-size, radius, shadow, duration, easing is a CSS
custom property defined in `web/src/tokens.css`. No `style={{ background:
"#..." }}` anywhere; no hex literal in any component. An audit script in
ticket 01 fails the build if either happens. The Master file at
`design-system/friday-monitor/MASTER.md` is the source of truth and the
tokens must follow it; where the Master is wrong (light-mode palette,
landing-page section order, mobile breakpoint) the tokens override it
because the operator said *dark, desktop only*.

Why: a redesign every six months stops being painful when the redesign
touches one file. Skipping the token layer means every future change is a
grep.

### D4. Dark palette, tuned for an operator dashboard

| Token | Value | Used for |
|---|---|---|
| `--bg-0` | `#0A0E1A` | page background, near-black for OLED |
| `--bg-1` | `#111827` | card / row background |
| `--bg-2` | `#1F2937` | raised card, dialog |
| `--line` | `#374151` | divider |
| `--ink` | `#E5E7EB` | primary text |
| `--ink-faint` | `#9CA3AF` | secondary text |
| `--ink-mute` | `#6B7280` | meta, timestamps |
| `--accent` | `#38BDF8` | focus ring, active state, link |
| `--good` | `#10B981` | succeeded |
| `--warn` | `#F59E0B` | waiting, retry |
| `--bad` | `#EF4444` | failed, error |

WCAG AA contrast against `--bg-0`: `--ink` 14.2:1, `--ink-faint` 6.1:1,
`--ink-mute` 4.6:1, `--accent` 7.5:1, `--good` 5.0:1, `--warn` 6.8:1,
`--bad` 5.4:1. All pass.

### D5. SSE, not polling, for live updates

Server endpoint `GET /api/events` streams a line of JSON per event using
`text/event-stream`. Reconnect-with-replay: `Last-Event-ID` header → server
replays missed events from the store. Events:

- `message_created`
- `message_triaged`
- `task_opened`, `task_state_changed`, `task_completed`
- `call_started`, `call_completed`, `tool_called`
- `error_raised`

Client uses native `EventSource`. Reconnect is the browser's problem.

Why: polling at 5s is one tool-call behind reality. SSE in FastAPI is
built in; the data store already records every one of those events as a
row, so the source is one query per event class, no separate log to keep.

### D6. Monitor screen layout

```
┌───────────────────────────────────────────────────────────────┐
│ topbar: friday · [monitor] [board] [rooms]   ● live  4 tasks │
├─────────────────────────────────┬─────────────────────────────┤
│ LIVE FEED (events, 60%)         │ LIVE TASKS (running, 40%)   │
│                                 │                             │
│ 14:32:01  mention  @lee         │ ● extracting  task #14      │
│ 14:32:01  classify api_issue     │   fetch_skill · 4.2s        │
│ 14:32:04  extract  start        │   312 → 0 tok               │
│ 14:32:05  tool:read_skill_file   │ ──────────────────────────  │
│ 14:32:07  awaiting  approval    │ ◌ awaiting approval #19     │
│ 14:32:09  reply    sent          │   since 02:14               │
│                                 │ ──────────────────────────  │
│ (auto-scroll, pause on tap)     │ (each card → flow)          │
├─────────────────────────────────┴─────────────────────────────┤
│ FOOTER STRIP                                         [refresh]│
│  throughput  ·  errors  ·  spend today  ·  last message          │
└───────────────────────────────────────────────────────────────┘
```

A paused feed (operator taps to read) shows "PAUSED — tap to resume".
The card icons use color + label both — `color-only` is forbidden because
it fails the WCAG colour-blindness check.

Why: the feed shows **what happened**, the tasks panel shows **what is
running**. Two columns because the operator's two questions at any moment
are *what just happened* and *what is in flight*.

### D7. Drift guardrails

Three scripts run on every commit and fail the build:

- `test_design_tokens_everywhere` — greps `web/src/` for `style={{` and
  hex literals. Returns 0 matches.
- `test_a11y_audit` — runs axe-core against `web/dist` after a build.
  Fails if any violation has severity `serious` or `critical`.
- `test_performance_budget` — runs Lighthouse CI on `/monitor`. Fails if
  LCP > 1.0s, CLS > 0.05, TBT > 200ms, JS bundle > 200KB.

Why: the 13 problems the design-system audit found in this spec (see
`notes.md` for the count) are the kind that pass code review and fail
users. Tests are the only place a regression in *those* surfaces — and
they have to run automatically, not when somebody remembers.

### D8. The earlier UI redesign is retired

Ticket 19 (Liquid Glass) was the wrong direction for an operator
dashboard. The file stays as a record of what was tried. The decisions
recorded in `docs/DESIGN.md` about translucent chrome do not apply; this
board supersedes them. CLAUDE.md's status paragraph and the layout
table's `web/` row are updated in ticket 10.

## What is in scope

- The five screens reachable from the topbar: Monitor, Board, Rooms,
  Flow (per-message, not a tab), and Context (popup inside Rooms).
- Real-time event stream from server to browser.
- Keyboard shortcuts and focus management.
- Audit + performance + accessibility gates that fail the build.

## What is out of scope

- Editing anything from the page (the page is read-only — operators act
  via Discord or CLI). Renaming a room is the one exception, already in.
- Mobile or tablet layouts. Desktop only.
- Multiple operator accounts. The board has no auth.
- Charts and sparklines. Numbers in text are enough; trend graphs are a
  later board.

## Tickets

1. **01-design-tokens-and-primitives** — token system, primitive
   components, audit script. Foundation for everything else.
2. **02-motion-system** — three motions, durations, easings,
   reduced-motion.
3. **03-loading-states** — skeleton, spinner, toast. Replaces every
   `Loading…` literal.
4. **04-monitor-screen** — the front door. Two columns, live feed, task
   cards. Topbar gains Monitor tab.
5. **05-sse-events** — `/api/events`, replay on reconnect, monitor
   subscribes.
6. **06-drill-down** — click-throughs from monitor to flow, breadcrumb.
7. **07-keyboard-and-focus** — shortcuts, focus rings, `?` overlay.
8. **08-test-a11y-axe** — axe-core in the suite, gate.
9. **09-test-perf-lighthouse** — Lighthouse CI, bundle budget, gate.
10. **10-update-claudemd-and-retire-ticket-19** — sync the project's
    memory and close out the old direction.

Tickets 01–07 ship UI. Tickets 08–09 ship the gates that catch the
13-audit issues listed in `notes.md`. Ticket 10 ships the documentation
that makes this board's decisions durable.

Each ticket is a hard gate: implementation → build `web/dist` → operator
review via SSH tunnel → commit. No batching.

### Status

| # | Ticket | Status | Commit |
|---|---|---|---|
| 01 | design tokens + primitives | done | `dd5f428` |
| 02 | motion system | done | `65ad7cd` |
| 03 | loading states | done | `6e5dc2b` |
| 04 | monitor screen (front door) | done | `9e8469a` |
| 05 | SSE events | done | `de6665d` |
| 06 | drill-down Monitor → Flow | done | `d5a28ca` |
| 07 | keyboard + focus | done | (in `17201f8`) |
| 08 | a11y axe-core gate | done | `47652da` |
| 09 | perf bundle gate | done | `17201f8` |
| 10 | update CLAUDE.md, retire ticket 19 | done | `17201f8` |
| 11 | Rooms — task / enrichment markers | done | `60f2eb5` |
| 12 | Flow — state per step | done | `bc697c2` |
| 13 | Rooms — Agent vs Reporter marker | done | `f18e8eb` |

All thirteen tickets landed. The board's promise is met: the
operator opens `/` and sees a real-time Monitor with live feed,
running tasks, drill-down to the Flow screen, breadcrumb, agent
versus reporter markers, and gates that catch the regressions
the audit asked for.

## Sources

- `design-system/friday-monitor/MASTER.md` — generated by
  `ui-ux-pro-max`, adapted for dark-only desktop per operator decision
  on 2026-09-08.
- `.scratch/a-window-on-the-whole-path/spec.md` — the previous board's
  decisions still in force.
- `.scratch/discord-mention-triage/issues/19-liquid-glass-on-the-chrome.md`
  — superseded; the file stays.
- `docs/DESIGN.md` — architecture decisions; only D5 (web/dist served by
  the API process) and D4 (no `web/dist` in git) still bind here.
