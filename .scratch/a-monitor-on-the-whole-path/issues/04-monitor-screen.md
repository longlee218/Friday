# 04: Monitor screen — the front door

**What to build:**

A new screen `web/src/screens/MonitorScreen.tsx` and route
`web/src/router.tsx` (new) wiring it as `/`. Topbar gains a third tab
"Monitor" before "Board". The existing `BoardScreen` becomes
`/board`; nothing else moves.

Layout per spec D6: topbar / live feed (60%) / live tasks (40%) /
footer strip. Cards carry color + label both (audit #2). Feed uses
`role="log"` and `aria-live="polite"` (audit #1). Task cards are
`React.memo`'d on `task.id` so SSE bursts do not re-render the whole
list (audit #4).

The feed virtualizes when its row count exceeds 200; below that it
just appends. Below 1280px the layout collapses to a tabbed view
between Feed and Tasks.

A new endpoint `GET /api/monitor` returns the initial snapshot: 100
recent events + currently running tasks + footer counters. SSE (ticket
05) takes over from there.

**Blocked by:** 01, 02, 03.

**Decisions:** D1, D6.

**Status:** ready-for-agent
