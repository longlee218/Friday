# 04: Monitor screen — the front door

**What to build:**

The operator's first question on opening the page is "what is
happening right now", and the answer is two questions: "what just
happened" (the feed) and "what is in flight" (the tasks). The
Monitor screen renders both in two columns with a footer strip
of the throughput numbers.

**Backend:**

- `Database.running_tasks()` returns tasks in `OPEN` states with
  `last_activity_at`, `last_tool`, `attempts` populated via batched
  subqueries (no N+1).
- `Database.recent_events(limit)` merges the last `limit` rows from
  `model_calls` and `tool_calls` sorted by `created_at`.
- `Database.monitor_snapshot()` folds events, running tasks, and the
  day's spend into one round trip — five answers the page asks in
  the same breath.
- `/api/monitor` returns the snapshot. One endpoint, one request,
  five spinners replaced by one.

**Frontend:**

- `MonitorScreen.tsx` renders the live feed (60%) with
  `role="log"` and `aria-live="polite"` (audit #1), and the
  running tasks panel (40%) with a `React.memo`'d `TaskCard`
  (audit #4). Cards carry state pills with both label and tone
  (audit #2).
- Virtualization activates only above 200 rows — below that, a
  virtualized list is a regression in interaction, not a feature.
- The topbar gains Monitor as the first tab. `/board` becomes
  the explicit path; the operator who typed `/` lands on Monitor,
  not Board, which is the question the operator answers first.

**Blocked by:** 01, 02, 03.

**Decisions:** D1, D6.

**Status:** done

- [ ] `Database.monitor_snapshot()` returns a `MonitorSnapshot`
      domain object with `status`, `events`, `running_tasks`, the
      three counters, and the day's spend. Counts come from
      `Database.counts()`; events from `recent_events(100)`;
      running from `running_tasks()`.
- [ ] `/api/monitor` returns the snapshot via a single
      `_clean()` call. No new endpoint besides this one; the
      page's mount asks once.
- [ ] `MonitorScreen.tsx` renders the two-column layout. Below
      1280px it collapses to a tabbed view between Feed and
      Tasks (CSS media query).
- [ ] The feed section uses `role="log"` and `aria-live="polite"`
      (audit #1). `TaskCard` is `React.memo`'d on `task.id`
      (audit #4). State pills carry both label and tone (audit #2).
- [ ] `toneFromState()` lives in `web/src/ui/state.ts` as a
      parallel to `flowState.ts`'s map.
- [ ] Three grep guards in `test_web_tokens.py`, all
      mutation-tested: dropping `role="log"`, the `memo` wrapper,
      or the Monitor tab flips its guard red.
- [ ] `npm run build` produces a single CSS and JS bundle; the
      ticket 09 budget is enforced by a separate gate.
