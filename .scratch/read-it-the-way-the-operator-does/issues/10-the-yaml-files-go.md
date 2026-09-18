# 10: The YAML context files go

**What to build:** Every reader of `ChannelContext` reads rows instead, and
`friday/memory/channel_context.py`'s store, its two write paths and the
files themselves are deleted rather than kept beside the table.

**Blocked by:** 09.

**Decisions:** spec, "Memory: one store, twelve kinds". Reverses the
2026-09-01 three-store split.

**Status:** done

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

## Done

- **The summary is a row.** `ContextRebuilder` (kept in
  `friday/memory/channel_context.py`, which lost the store) writes one active
  `summary` row per watched channel through `memory_add`, and a rebuild is
  `memory_supersede` with new `data` — so the history the file never had now
  exists. The bookmark (`summary_from`, `summary_of`, `summary_version`) is on
  the row as `SummaryData`, a subclass of `RoomSummary` in
  `friday/domain/models.py`; `channel_derived` reads `RoomSummary`'s four
  fields by name, byte-identical to the old `derived: {summary: ...}`
  rendering. The rooms considered are `config.ingest.watched_channels` (they
  were "channels with a file"). A refused write (ceiling, guard on the
  topic) leaves the previous summary standing and logs once.
- **Store:** `Database.room_summary`, `Database.knows_person` (a `person`
  row keyed on the Discord id, here or `'*'`), `domain_memories` now includes
  `channel_id='*'`, and `memory_supersede(data=...)`.
- **Readers, by `readers_for`:** triage reads the summary row only
  (`LightContext.summary`; `Triage(summaries=...)`, `build_light_context` is
  async). The extractor reads `domain_memories` (room + `'*'`, all origins);
  `FullContext.room` and every `context_store` parameter through
  `prepare_node`/`build_simple_dag`/`register_dags` are gone. `room_facts`
  now renders rows — plain, flattened, labelled by origin ("the operator
  wrote" first, then "remembered"); `remembered_facts` is folded into it.
  The responder reads the summary row only: `channel_base` and
  `channel_overrides` are deleted, and its job text no longer describes
  `register`/`people:`. `Responder.knows` is gone; `Pool._stranger` asks
  `db.knows_person`.
- **Deleted:** `ContextStore`, `ChannelContext`, `set_overrides`,
  `init_channel`, `rebuild_derived`, `channel_sections`, `init_channel.py`,
  `context/1544369941296189591.yaml` (it was `derived: {}`,
  `overrides: {}`), `config.yaml`'s `context.directory` and
  `ContextConfig.directory`, `build_api(context_store=)` with all five routes
  it mounted (`/api/channels`, the three `/api/channels/{id}/context*`,
  `/api/context/reload`), `ContextPanel.tsx`, the "What it is told" button,
  `ChannelContext` in `api-types.ts`, the five calls in `api.ts`, and the
  panel's CSS. `tests/test_context_writing.py` is deleted (store and routes).
- **Migration script:** `import_context_files.py [dir]` reads any YAML it
  finds: `base.yaml` → `'*'` `fact` rows, `overrides` → `fact` rows
  (`key: value`), `people:` entries shaped `{name, role, team}` → `person`
  rows keyed on the entry's id, other entries → `fact` rows. `derived` and
  `state` are not imported (the summariser rebuilds). Each value is also
  checked by the guard on its own, because `key: ` in front of a directive
  walks it past the first-word check.
- **Verify:** prefix stability — `test_a_room_with_no_rows_costs_triage_not_a_byte`
  and `test_a_room_with_no_rows_leaves_the_extractors_prompt_as_it_was` (new),
  plus the existing triage prefix tests rewritten onto rows, all green.
- Suite: `1 failed, 1234 passed, 1 skipped` — the one failure is the
  baseline `test_doc_paths_resolve_to_existing_files` (`friday/board/`).
  `cd web && npm run build` type-checks and builds.
- Guards reddened, each restored: `channel_derived` rendering all of `data`
  → bookmark test red; `'*'` dropped from `domain_memories` → extractor test
  red; `knows_person` ignoring the key → red; the operator label swapped →
  3 red; the importer's per-value guard removed → red; `memory_supersede`
  ignoring `data` → red; `knows_person` bypassed in `Pool._stranger` →
  `test_being_written_down_for_the_room_makes_them_known` red; the
  "nothing new" check removed from the summariser → red; a `ChannelContext`
  interface put back in `api-types.ts` → web contract red; a `/context`
  route put back → route test red.
- **Triage eval** (`uv run python -m evals.run_triage_eval`, 35 rows),
  against a fresh migrated database with no summary rows — so the prompt
  is byte-identical to before and any difference is the model. Run on this
  change and, for comparison, on its parent commit (a `git archive` copy):

  | | accuracy | api_issue→doc_question | api_issue→skip | below 0.5/0.6/0.7/0.8/0.9 | out of set |
  |---|---|---|---|---|---|
  | before (HEAD) | 82.9% | 3 | 2 | 3/4/8/14/19 | 0/35 |
  | after | 82.9% | 2 | 3 | 1/5/9/14/21 | 0/35 |

  After, in full:

      35 examples, accuracy 82.9%
      confusion (rows: expected, columns: predicted)
                      access_request  api_issue  doc_question  needs_human  skip
      access_request  4               0          0             0            0
      api_issue       0               18         2             0            3
      doc_question    0               0          3             1            0
      needs_human     0               0          0             0            0
      skip            0               0          0             0            4
      confidence below threshold -> escalated to a human:
        0.5: 1/35  0.6: 5/35  0.7: 9/35  0.8: 14/35  0.9: 21/35
      decisions outside the closed set: 0/35

- **A consequence to know about:** following `readers_for`, the responder no
  longer receives the operator's facts or a room's `register`/`people:` by
  injection — how a room is spoken in is a `voice` row it reaches through
  `memory_search`, and a person is read by code. No install had any (the one
  file was empty), so nothing is lost today; ticket 40's per-room register
  test now proves per-room *summaries* instead.
- **Not done:** no `code-review` subagent pass — this lane has no agent tool
  to spawn one. The review is owed.

## Docs owed

`CLAUDE.md` (not editable from this lane):

- Layout: the `friday/memory/` row ("tiers that never mix:
  `channel_context.py` (per-channel YAML)…") — it is the summariser only, and
  every memory is a row. Remove the `init_channel.py` row; add
  `import_context_files.py` (one-off import of a second install's files).
- The Rooms/`web/` and `friday/ops/` rows: the board writes no context file;
  the three `/api/channels/{id}/context*` routes, `/api/channels` and
  `/api/context/reload` are gone, and so is `ContextPanel`.
- "A channel is summarised when the room has said more": the mark is the
  `summary` row's `data` (`SummaryData`), not a `state` section outside
  `derived`; a rebuild supersedes the previous row; the rooms considered are
  the watched channels.
- "Anything this system stores and later reads back into a prompt is stored
  plain" / "`channel_derived` escapes once": `channel_derived` takes the
  summary row and reads `RoomSummary`'s four fields by name.
- "Triage is shown the turn and the room's summary": `LightContext(turn,
  summary)`; `build_light_context(summaries, …)` is async and reads
  `Database.room_summary`; no `base`/`overrides` exist to keep out.
- "Node 0's own build …" (ticket 15 paragraph): `context_store` no longer
  reaches `build_full_context`; `FullContext` has no `room`; the operator's
  rows arrive in `domain_memories` (room + `'*'`), rendered by `room_facts`
  labelled by origin.
- The memory-guard paragraph names `ContextStore.set_overrides`/`init_channel`
  as write paths; they are gone — the operator's hand goes through
  `memory_add` via the memory routes.
- The responder's section order / "One persona" material: the responder has
  `channel_derived` only (no `channel_base`/`channel_overrides`).
- `Pool._stranger` asks `Database.knows_person` (a `person` row) where it
  asked `Responder.knows` (a `people:` name).
