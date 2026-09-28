Status: ready-for-agent
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

- [ ] No import of the old module paths remains; no re-export shim.
- [ ] The only module importing `pydantic_ai` is still `harness.py` (guard).
- [ ] Each new file has one responsibility; none of the four new sets over
      ~200 lines without a reason noted.
- [ ] `docs/DESIGN.md` § What exists / Layout updated for the moved files.
- [ ] Whole suite green (same count as before, no test logic changed);
      `code-review` done.
