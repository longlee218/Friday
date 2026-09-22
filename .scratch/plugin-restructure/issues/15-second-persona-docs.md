# 15: Second persona — plugins/docs

**What to build:** A second persona is added as `plugins/docs` (`doc_question`, already a one-node graph) with zero kernel diff — the proof that a new persona needs no core edit.

**Blocked by:** 14.

**Source:** `spec.md` — Migration order, step 8 (second persona `plugins/docs/`).

**Status:** ready-for-agent

- [ ] `plugins/docs` registers `doc_question` (one-node graph) through `register()`
- [ ] It works end-to-end through the S1 message-path seam (message → triage → task → draft → approval)
- [ ] The diff touches no file under `friday/kernel` (verified)
- [ ] `uv run pytest -q` passes
