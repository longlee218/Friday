# 06: Drill-down paths

**What to build:**

Click a live task card → URL becomes `/flow/{provider}/{message_id}`.
Click an event in the feed (a model_call or tool_call row) → URL
becomes `/flow/{provider}/{message_id}#call-{call_id}` and the matching
card in the flow page scrolls into view and gets a focus ring.

A `<Breadcrumb>` component in `web/src/ui/` appears on every screen
that is not the Monitor. Format: `monitor › task #14 › call #22`. The
breadcrumb collapses anything beyond 4 levels into `...`.

The "Path" tab that lived in the previous board is gone. The flow
screen is reached only from the Monitor — the topbar does not advertise
it.

**Blocked by:** 04, 05.

**Decisions:** D1, D6.

**Status:** ready-for-agent
