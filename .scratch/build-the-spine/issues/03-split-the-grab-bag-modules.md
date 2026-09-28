Status: done
Blocked by: 02

# Split the grab-bag modules

Decision: [The target module layout](../../domains-plug-in/issues/09-the-target-module-layout.md) §4–7, §10
(target tree: `.scratch/domains-plug-in/target_module_layout_STUB.md` on
branch `prototype/target-module-layout`). Pure moves — no behaviour change.

## Goal

- `kernel/domain/models.py` → `messages.py`, `tasks.py`, `outbound.py`,
  `memory.py`, `monitor.py`, `state.py`.
- `kernel/harness/harness.py` → `harness.py` (loop), `retry.py`,
  `model_client.py`; `@tool` and `Refused` move to `friday/sdk/toolset.py`
  (harness imports them; no re-export).
- `store/repositories/memory.py` → `memory.py` + `memory_candidates.py`.
- `store/repositories/tasks.py` → `tasks.py`, `task_conversation.py`,
  `compaction.py`.
- One-file packages fold: `kernel/inbox.py`, `kernel/outbox.py` +
  `kernel/outbox_card.py`, `kernel/text_transform.py`. (`pool/` folds in 14,
  `dag/`/`extraction/` are deleted in 16.)
- `development-rules.md`: 200 lines is a soft target; `sdk/` holds only
  what a plugin imports.

## Acceptance

- [x] No import of the old module paths remains; no re-export shim.
- [x] The only module importing `pydantic_ai` is still `harness.py` (guard).
- [x] Each new file has one responsibility; none of the four new sets over
      ~200 lines without a reason noted.
- [x] `docs/DESIGN.md` § What exists / Layout updated for the moved files.
- [x] Whole suite green (same count as before, no test logic changed);
      `code-review` done.

## Done (2026-09-28)

- **`@tool` / `Refused` — operator's call, 2026-09-28: rename only.** The
  harness's `tool` builds the vendor's `Tool`, so moving it into `sdk/` would
  break the pydantic_ai guard; `Refused` went with the budget in ticket 01.
  `friday/sdk/tools.py` became `friday/sdk/toolset.py` (the neutral `tool` /
  `ToolSpec`); the harness `tool` stays in `harness.py` until ticket 08 moves
  the core tools onto `ToolSpec`.
- `_chat_model` stays in `harness.py` (it names the SDK's chat model);
  `model_client.py` holds the `AsyncOpenAI` client it wraps. `_transient`
  lost its explicit `UnexpectedModelBehavior` / `UsageLimitExceeded` check:
  neither is a subclass of a retried class, so both still return `False`.
- `Params`, `SKIP`, `askable_fields`, `ExtractionMark` sit in
  `domain/tasks.py` until ticket 16 deletes them. `text/param_hygiene.py`
  stays in `text/` for the same reason.
- Over ~200 lines, reason in each docstring: `domain/memory.py` (280),
  `domain/tasks.py` (202), `repositories/memory.py` (645),
  `repositories/task_conversation.py` (443), `repositories/tasks.py` (235),
  `harness/harness.py` (723).
- Suite 1579 passed, 1 skipped — the same as before; test diffs are imports
  and three prose paths. The pydantic_ai guard was watched red (an import
  added to `retry.py`) and green again.
- `code-review` (subagent): no behaviour change found. It also reported a
  pre-existing bug, confirmed in `pydantic_ai/models/openai.py` `_map_api_errors`:
  the SDK wraps `APIStatusError` / `APIConnectionError` as `ModelHTTPError` /
  `ModelAPIError`, so `retry._transient` likely never retries a real 429 / 5xx /
  connection error (only timeouts). The tests fake the openai error directly.
  Not fixed here: out of scope for a pure move.
