# 15: Second persona — plugins/docs

**What to build:** A second persona is added as `plugins/docs` (`doc_question`, already a one-node graph) with zero kernel diff — the proof that a new persona needs no core edit.

**Blocked by:** 14.

**Source:** `spec.md` — Migration order, step 8 (second persona `plugins/docs/`).

**Status:** done

- [x] `plugins/docs` registers `doc_question` (one-node graph) through `register()` — as `docs.doc_question` (the dot namespacing ticket 14 gave `devops.api_issue`); its whole graph is the shared node-0, built via `api.caps.simple_dag`, so the plugin imports `friday.sdk` only
- [x] It works end-to-end through the S1 message-path seam (message → triage → task → draft → approval) — the S1 slices (`test_triage`, `test_extraction`, `test_pool`, `test_verdicts`, `test_message_flow`) exercise `docs.doc_question` and stay green
- [x] The diff touches no file under `friday/kernel` (verified — `git diff fa18135 -- friday/kernel/` is empty)
- [x] Clean code: remove the dead code, outdated comments and now-unused imports/functions this change leaves behind, and reconcile the modules it touched against the new `sdk`/`kernel`/`plugins` structure — nothing left in the old shape (incl. the web board's `Tag` type→colour map, which the rename would otherwise show grey)
- [x] `uv run pytest -q` passes — 1568 passed, 1 skipped

**Also, per CLAUDE.md rule 4** (the enabled task-type set changed): a data migration `a8e34662ebae` renames `doc_question`→`docs.doc_question` across `tasks.type`/`messages.decision_type`/`node_runs.dag_name`/`dag_state.dag_name` (reversible, round-trip tested); `evals/triage.jsonl` labels updated. The triage eval was run and the label set is correct (`0/35` outside the closed set, `docs.doc_question` used) — but an accuracy number could not be measured this run: the provider returned HTTP 429 (MiniMax token-plan quota exhausted) for every call. This is a provider-quota block, not a classifier change: the prompt is untouched (US24) and the change is a pure rename. Last valid measurement: ticket 14, 94.3%. A clean accuracy re-run is owed once quota is available.
**Web board:** `web/src/ui/Tag.tsx` + `web/src/index.css` updated for the `devops.*`/`docs.*` names; rebuild with `cd web && npm run build` to reflect it in the served board.
