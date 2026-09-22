# 05: Pydantic AI harness + ModelProvider seam

**What to build:** The agent loop runs on Pydantic AI behind a `ModelProvider`/harness seam, with no change in behaviour — the operator maintains invariants and glue, not a hand-wired harness, and swapping or adding a provider later is an adapter, not a rewrite.

**Blocked by:** 01. (Recommended before 06 to avoid re-touching the node/harness seam; not a hard blocker.)

**Source:** `spec.md` — Migration order, step 2 (Pydantic AI + ModelProvider seam); § Implementation Decisions → "Runtime libraries" (DESIGN-v2 §6.7). See `docs/research/pydantic-ai-migration.md`.

**Status:** ready-for-agent

- [x] `harness.py` runs the agent loop on Pydantic AI and is the only module importing the agent/vendor SDK
- [ ] A `ModelProvider`/harness seam is drawn; moving between compatible vendors is `base_url`/`api_key`/`model`
- [x] The vendored `ScriptedModel` test double is replaced by a Pydantic-AI equivalent under `sdk/testing/` (seam S2)
- [x] Structured output + the one correction turn still work
- [x] The kernel chain wraps budget, redaction and recording around every model call
- [x] `uv run pytest -q` passes
- [x] Triage eval re-run and reported (accuracy, confusion matrix, threshold table) — the harness is upstream of the classifier

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

**2026-09-22 — Slice 2b landed, and it absorbed slice 3's MCP + delete (operator's call).**
The harness runs on Pydantic AI. `harness.py`/`llm_log.py`/`mcp.py` are the
only vendor importers (`pydantic_ai`, `fastmcp`), plus `friday/sdk/testing/`;
`openai-agents` is removed from `pyproject.toml`/`uv.lock`. Suite green: 1604
passed, 1 skipped (was 1608 — the 4 checkpoint/resume tests are gone). mypy
clean on the three swapped modules.

What changed, against the research doc's feature map:

- **Structured output** is a Pydantic AI `ToolOutput` whose function takes the
  model's raw arguments and validates with `fits` (not the framework's own
  pydantic validation, whose `RetryPromptPart` would quote the offending
  value — verified by spike). One correction is `retries={'output': 1}`; the
  forced output tool means no text output is allowed, so the run cannot answer
  in prose. D13's written-answer fallback is preserved by reading the run's
  captured messages (`capture_run_messages`) when the forced tool produced no
  fitting answer.
- **Hooks** are a per-run `Hooks` capability handed to `agent.run(capabilities=)`
  — which fixes the shared-`agent.hooks` bug natively (two runs of one harness
  no longer clobber each other). Tool-failure "unavailable, carry on" moved to
  the `tool_execute_error` hook, with a guard so `after_tool_execute` does not
  record the substituted result twice.
- **MCP** (absorbed): `mcp.py` builds `MCPToolset` over fastmcp transports,
  allow-listed with `.filtered()`, `tool_error_behavior='failed'`, `SsoTokens`
  as the httpx2 `auth`. `name_of()` reads a filtered toolset's name off the
  `MCPToolset` it wraps. `run_agent.py` enters and keys them by that name.
  *Not verified against the live Keycloak server* — that is an operator action
  needing credentials; the research-doc spike already proved catalogue + auth.
- **Deleted** (absorbed from slice 3): `Harness.checkpoint`/`resume`/`RunState`
  and `needs_approval` — no prod caller; only `test_harness` exercised them.

Two boxes left unticked:

- **ModelProvider seam (item 2).** Its *substance* holds — moving between
  compatible OpenAI vendors is `base_url`/`api_key`/`model` (`_chat_model`),
  and moving to Anthropic/Gemini is swapping `OpenAIChatModel` for the
  framework's own model class, an adapter not a rewrite. A *bespoke*
  `ModelProvider` port was deliberately not built, per the research doc
  ("Pydantic AI makes DESIGN-v2 §6.7's ModelProvider port mostly
  unnecessary"). Left for the operator to confirm the framework's provider
  abstraction satisfies the intent.
- **Triage eval (item 7).** CLAUDE.md rule 4 applies (the harness is upstream
  of the classifier), but the eval scores the *live* classifier — a billed
  provider call the operator triggers: `uv run python -m evals.run_triage_eval`
  against `evals/triage.jsonl`. Not run here; still slice 4's box.

**2026-09-22 — Item 7 done: triage eval re-run on the new harness, no
regression.** `uv run python -m evals.run_triage_eval` (on the pydantic-evals
runner, ticket 18): **35 examples, accuracy 100.0%**, confusion matrix a clean
diagonal (access_request 4/4, api_issue 23/23, doc_question 4/4, skip 4/4), zero
out-of-set. Thresholds (below → escalate): 0.5→1, 0.6→2, 0.7→4, 0.8→6, 0.9→13.
Matches the pre-swap known-good (100% since the 2026-09-20 label rewrite), so
the prompt-shape change the swap introduced — the answer tool's per-field schema
now conveyed in the tool *description* rather than as wire JSON schema — did not
cost the classifier anything. **Only item 2 (ModelProvider port) remains** and
is a decision for the operator, so this ticket is not yet `done`.
