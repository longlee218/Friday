# 10: Update CLAUDE.md, retire ticket 19

**What to build:**

The project's own documentation is part of the change. CLAUDE.md
describes the current state; the previous board
(`.scratch/discord-mention-triage/`) carries decisions that no
longer apply. Ticket 10 makes the docs honest.

Three edits to `CLAUDE.md`, all in the same commit as ticket 10 itself:

1. The status paragraph mentioning the open UI tickets is rewritten
   to point at this board, with the same words used here:
   *"18–20 were the previous direction (Liquid Glass + SSE in the
   board's old repo). Ticket 19 was retired in favour of
   `.scratch/a-monitor-on-the-whole-path/`, a 13-ticket board that
   redoes the UI as a real-time operator monitor dashboard.
   Tickets 20 (SSE) lands in ticket 05 of that board."*

2. The `web/` row in the layout table is rewritten to describe the
   monitor dashboard, motion system, and dark-palette tokens.

3. A new `Architecture constraints` entry summarises this board's
   D5 (SSE) decision in two lines: events come from the store rows
   the system already writes; one writer enqueues onto one queue.

Two edits to the old board:

- `.scratch/discord-mention-triage/issues/19-liquid-glass-on-the-chrome.md`:
  status flipped to `retired`, with a pointer to this board at the
  top.
- `.scratch/discord-mention-triage/issues/20-live-updates-without-polling.md`:
  status flipped to `done`, pointing to ticket 05 of this board.

Ticket 18 stays `ready-for-agent` — the React+Vite-as-static-SPA
decision still holds; nothing in this board reverses it.

**Blocked by:** 04, 05, 06, 08, 09.

**Decisions:** D1, D5, D8.

**Status:** done

- [ ] `CLAUDE.md` status paragraph rewritten to point at
      `.scratch/a-monitor-on-the-whole-path/`, mention ticket 19
      retired, and note that ticket 20 lives in ticket 05 of this
      board.
- [ ] `CLAUDE.md` layout table's `web/` row describes the monitor
      dashboard, motion system, and dark palette. The old "two
      readers of one dataset" sentence is preserved — the SPA
      replaced the old `friday/board/`, but the principle stands.
- [ ] A new `Architecture constraints` entry under CLAUDE.md
      summarises the SSE decision in two lines (events come from
      the store rows; one writer enqueues onto one queue).
- [ ] `.scratch/discord-mention-triage/issues/19-...md` status is
      `retired`, with a one-paragraph pointer at the top to this
      board.
- [ ] `.scratch/discord-mention-triage/issues/20-...md` status is
      `done`, pointing to ticket 05.
- [ ] `tests/test_repo_hygiene.py` (or equivalent) asserts the
      CLAUDE.md status paragraph mentions this board. A future
      edit that drops the pointer without updating the docs is a
      regression the build catches.
