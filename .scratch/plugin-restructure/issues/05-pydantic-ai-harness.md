# 05: Pydantic AI harness + ModelProvider seam

**What to build:** The agent loop runs on Pydantic AI behind a `ModelProvider`/harness seam, with no change in behaviour — the operator maintains invariants and glue, not a hand-wired harness, and swapping or adding a provider later is an adapter, not a rewrite.

**Blocked by:** 01. (Recommended before 06 to avoid re-touching the node/harness seam; not a hard blocker.)

**Status:** ready-for-agent

- [ ] `harness.py` runs the agent loop on Pydantic AI and is the only module importing the agent/vendor SDK
- [ ] A `ModelProvider`/harness seam is drawn; moving between compatible vendors is `base_url`/`api_key`/`model`
- [ ] The vendored `ScriptedModel` test double is replaced by a Pydantic-AI equivalent under `sdk/testing/` (seam S2)
- [ ] Structured output + the one correction turn still work
- [ ] The kernel chain wraps budget, redaction and recording around every model call
- [ ] `uv run pytest -q` passes
- [ ] Triage eval re-run and reported (accuracy, confusion matrix, threshold table) — the harness is upstream of the classifier

## Comments

**2026-09-22 — Slice 1 of the staged migration landed (research doc step 2).**
The vendored test doubles are now behind a Friday-owned seam,
`friday/sdk/testing/`, and all 14 test files import from it rather than from
`agents.*`. Green-to-green, no behaviour change (suite 1608 passed, 1 skipped,
same count). The "only harness imports the vendor" guard was widened to allow
`friday/sdk/testing/__init__.py`.

No checklist box is ticked yet: the seam holds the *vendored* double
re-exported, not the Pydantic-AI equivalent, so item 3 is only half-done and
nothing else is started. Remaining slices, in order:

- **Slice 2 — harness swap.** Adopt `pydantic-ai-slim[openai,mcp]` (bumps
  `openai` 3.6→≥3.8 in `uv.lock`), swap `harness.py`/`structured.py` internals
  behind the same API, re-point the seam's insides at `FunctionModel`. **Risk
  noted in review:** a few tests roll their own `agents.models.interface.Model`
  subclass and import `openai.types.responses` directly (not through the seam)
  — those need rewriting by hand in slice 2, the seam swap alone won't move
  them. Covers items 1, 3, 4, 5.
- **Slice 3 — MCP** (`mcp.py` → `MCPToolset`), the `ModelProvider` seam (item
  2), delete the unused checkpoint/resume path.
- **Slice 4 — measure:** triage eval + captured-case replay (items 6, 7).
