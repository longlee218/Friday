# 01: The board and its data are cleared away

**What to build:** Nothing. Delete `friday/board/`, wipe the database, and fix
every document that describes either of them.

**Blocked by:** None

**Decisions:** D1, D12

**Status:** done

## Why

`friday/board/__init__.py` is 196 lines of f-string HTML with an HTMX tag
polling every five seconds. It is not the thing being replaced *by* the SPA —
it is the thing that must not exist *alongside* it, and this repo has paid for
that lesson twice already.

The first time is in `CLAUDE.md`: *"the last time this codebase had two
renderers, one of them did not escape — a skill described as
`harmless</skills>` closed the section."*

The second time is live and was found while writing this board's spec.
`friday/ops/api.py` runs every response through `_clean()`, eleven call sites,
because *"This is the last place anything leaves the process, so it is the last
place a credential can be caught."* `friday/board/__init__.py` calls it zero
times, and renders `last_error` — which begins life as a provider exception —
and whole prompts. `friday/ops/redact.py` exists because the Discord user token
is unscoped account access that must never reach a log or a traceback. One of
the two renderers of that data scrubs it. The other does not.

The gap is **not patched first.** Fixing a file that this ticket deletes spends
the work on the wrong artefact; the fix is the deletion.

**The database goes with it (D12).** The operator's decision, taken with the
counter-argument in hand: 384 messages, 22 tasks, 137 model calls and 58
outbound rows *would* have rendered in the new UI — they lack only the
per-task and per-node correlation, since they predate the 2026-09-06
migration. Recorded so a later reader does not mistake it for an accident.

Two backups hold that data, and the difference matters if either is ever
restored: `data/friday.db.backup-20260906-203748-pre-wipe` is the one to use —
same rows, schema already at head. `data/friday.db.backup-20260906-184554-pre-migration`
is the same rows at the *old* schema, eleven migrations behind, kept only
because it is what the database looked like before anything on the previous
board had ever run against it.

## Acceptance criteria

- [x] `friday/board/` is gone, and nothing imports it — `serve_board.py`'s
      `app.mount("/", build_board(...))` included
- [x] `serve_board.py` still runs and still serves the JSON API alone, or is
      itself removed if it has nothing left to do — decide it here rather than
      leaving a script that starts a server with no pages
- [x] `run_agent.py` starts with no board task, and the process still comes up
- [x] The database is wiped and rebuilt from `alembic upgrade head`, and
      `alembic check` reports no drift afterwards
- [x] A fresh backup is taken immediately before the wipe, named in the
      convention `data/` already uses, and its row counts are printed before
      anything is destroyed
- [x] `CLAUDE.md`'s layout table no longer claims `friday/board/` exists; its
      "Board — :8086" description in `docs/DESIGN.md` is rewritten rather than
      left describing a deleted file
- [x] `docs/SPEC.md` stories 39–42 are annotated: what still holds, and that 42
      ("read-only") is narrowed by D7 rather than deleted
- [x] The whole suite passes with the board's own tests removed, not skipped

## Notes

`serve_board.py` is the interesting decision inside this ticket. Today it
builds the API and mounts the board under it. With the board gone it is "run
the JSON API against the live database without connecting to Discord", which
is still a useful thing to have while developing the SPA — probably keep it,
rename what it says about itself.

Do not delete `friday/ops/api.py`. It is the replacement's only data source and
it is already the scrubbed path.
