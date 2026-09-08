# 06: Drill-down paths — Monitor → Flow

**What to build:**

Two clicks get the operator from the Monitor screen to the
deepest card on the Flow screen:

- Click a running task → `/flow/{provider:message_id}`.
- Click a live feed event → `/flow/{provider:message_id}`, with
  the deep-link carried in the SSE payload.

The Flow screen reads `#call-{id}` on mount, scrolls the matching
card into view, and focuses it so a screen reader reads the
title — the audit's "the operator can always see *why*".

**Breadcrumb:**

- New `web/src/ui/Breadcrumb.tsx` primitive: monospace trail with a
  `›` separator. The last item is plain text (you cannot click
  where you already are). Anything beyond 4 items collapses to
  `head › … › tail`.
- `FlowScreen` renders `Monitor › task #N › flow`. The Monitor
  screen does not advertise the Flow — the topbar still has only
  Monitor/Board/Rooms — but the breadcrumb gives the operator a
  clickable link back to the front door.

**Blocked by:** 04.

**Decisions:** D1, D6.

**Status:** done

- [ ] `web/src/ui/Breadcrumb.tsx` exports a `<Breadcrumb>` primitive
      and a `BreadcrumbItem` type. The trail collapses anything
      beyond `MAX_ITEMS = 4` to `head › … › tail`.
- [ ] `web/src/router.ts` re-exports `navigate` from App. The
      Monitor screen calls `navigate(/flow/{message_id})` on
      task-card and event-row click.
- [ ] `MonitorScreen.tsx` adds `onClick` to `TaskCard` and
      `FeedRow` (with keyboard handler — Enter / Space). Both
      rows are `role="button"` when clickable.
- [ ] `FlowScreen.tsx` reads `window.location.hash` on mount. A
      `#call-{id}` anchor scrolls the matching card into view
      and focuses it. The card wrapper carries
      `id="call-{row.id}"` so the anchor resolves.
- [ ] `Database.source_message_of_task` returns
      `(provider, provider_message_id)` for a task. The SSE
      publisher calls it on `record_model_call` /
      `record_tool_call`; the wire carries `message_id` so the
      Monitor screen reads it without a second round trip.
- [ ] `RunningTask.message_id` is on the wire; the Monitor
      `TaskCard` clicks read it to drill into the flow.
- [ ] Three grep guards in `test_web_tokens.py`, all
      mutation-tested: dropping `MAX_ITEMS = 4`, the click
      handler on `TaskCard`, or the hash anchor in `FlowScreen`
      flips its guard red.
- [ ] `web/src/index.css` carries the breadcrumb layout. The
      separator is a real glyph (›), not a CSS-drawn slash.
