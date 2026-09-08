# 11: Rooms — message markers (task vs enrichment)

**What to build:**

The Rooms screen currently shows every message as a row of the same shape.
The operator's question on opening a room is sometimes *which message opened
a task* and *which message produced a memory* — the rest are ordinary
conversation. Two markers make that question answerable at a glance, and
clicking the row no longer opens the flow (that is what the timestamp is
for, since the message itself is rarely the thing the operator wants to
look at — the path that followed it is).

**Backend:**

- Add `memories.source_message_id` (nullable, indexed). `MemoryScope` gains
  a `message_id` field that `memory_add` carries through to the row. The
  field is read by the API and joined onto `messages`.
- `_message(...)` in `friday/ops/api.py` adds `is_task: bool` (from
  `task_id is not None`) and `is_enrichment: bool` (from the
  `memories.source_message_id` join).
- The store query that fetches room messages gains the join. One query,
  not N+1 — the existing `messages_in(limit=200)` already loads everything
  the Rooms screen needs, so the join rides on it.
- A test asserts that `source_message_id` is set when `memory_add` is
  called inside a turn that names its source message.

**Frontend:**

- `web/src/screens/RoomsScreen.tsx` renders two small markers on each
  message row: a task marker (✓) when `is_task`, an enrichment marker (✎)
  when `is_enrichment`. Both markers have an `aria-label`; the visual
  glyph is decoration.
- The whole-row click handler is removed. The only thing that opens the
  flow is the timestamp button — the same one the room already shows,
  with the same `class="time-link"` style introduced in ticket 01.
- A test pins the markers' presence on rows where the API says
  `is_task` / `is_enrichment`, and their absence on rows where it does
  not.

**Decisions:** D6 (monitor) and the operator's call on 2026-09-08 to
distinguish task from enrichment in the Rooms view.

**Status:** ready-for-agent
