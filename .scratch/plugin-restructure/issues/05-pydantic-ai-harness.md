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
