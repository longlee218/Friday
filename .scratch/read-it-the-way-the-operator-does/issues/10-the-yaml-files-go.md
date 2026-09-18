# 10: The YAML context files go

**What to build:** Every reader of `ChannelContext` reads rows instead, and
`friday/memory/channel_context.py`'s store, its two write paths and the
files themselves are deleted rather than kept beside the table.

**Blocked by:** 09.

**Decisions:** spec, "Memory: one store, twelve kinds". Reverses the
2026-09-01 three-store split.

**Status:** ready-for-agent

## Why this is a code move, not a data move

Measured 2026-09-17: `context/` holds one file,
`1544369941296189591.yaml`, and it is `derived: {}`, `overrides: {}`. There
is no `base.yaml`. Nothing the operator wrote is at risk. The migration
script still reads any file it finds and writes `origin=admin` `fact` rows
(and `person` rows from a `people:` mapping), because a second install may
not be empty.

## What moves where

- `base.yaml` → rows with `channel_id='*'`.
- `overrides` → `fact` / `constraint` / `person` rows, `origin=admin`.
- `derived` (the summariser's `RoomSummary`) → one active `summary` row per
  channel; a rebuild supersedes the last one, so the history the file never
  had now exists.
- `state` (the last summarised message id) → `data` on the `summary` row.
  It was kept out of `derived` because everything in `derived` is rendered;
  the renderer reads `RoomSummary`'s four fields by name and nothing else,
  and a test says the mark is never rendered.

## What is deleted

`ContextStore`, `set_overrides`, `init_channel`, `rebuild_derived`,
`init_channel.py`, `config.yaml`'s `context.directory`, the three
`/api/channels/{id}/context*` routes, and `ContextPanel.tsx`'s override
editor — replaced by ticket 09's form. `friday/memory/channel_context.py`
keeps the summariser and loses the store.

## What reads it today (each changes)

`friday/agent/instruction_prompt.py` (`channel_base`, `channel_derived`,
`channel_overrides`, `room_facts`), `friday/extraction/context.py` and
`prompt.py`, `friday/triage/context.py`, `friday/ops/api.py`,
`run_agent.py`, `serve_board.py`, and thirteen test modules.
`room_facts`'s three defences — plain, flattened, labelled by provenance —
carry over unchanged; the label becomes `origin` instead of a layer name.

## Verify

- Whole suite. The prefix-stability tests for triage and the extractor
  still pass: a room with no rows renders no section, byte-identical to
  today's unconfigured install.
- `uv run python -m evals.run_triage_eval`, reported — this is upstream of
  `friday/triage/prompt.py` (`CLAUDE.md`, Verifying a change, step 4).
- `CLAUDE.md` (Layout row for `friday/memory/`, the "three tiers" wording,
  the summariser paragraph), `CONTEXT.md` and `docs/DESIGN.md` corrected in
  the same commit.
