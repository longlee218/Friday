# Jev — a "System One" decision model (TypeSafe AI)

Researched 2026-09-22, one week after release. **Most numbers below are the
vendor's own**; the one independent evaluation found is Parallel's. Nothing
here has been measured against Friday's data.

## What it is

- TypeSafe AI's first "System One model": text or semi-structured state
  in, **typed decisions with probabilities** out. Early access opened
  2026-09-15. https://typesafe.ai/blog/introducing-system-one-models-and-jev
- Proprietary, API-only (`POST https://api.typesafe.ai/v1/systemone`, Python
  and TypeScript SDKs). **Not a `/chat/completions` model.** Also reachable
  through OpenRouter, LiteLLM pass-through, Pydantic AI and Cloudflare.
  https://docs.typesafe.ai/concepts/system-one ·
  https://docs.litellm.ai/docs/pass_through/typesafe ·
  https://openrouter.ai/typesafe
- Model id `jev-1.13.0` (`jev-latest`, `jev-preview`). 64k tokens per request
  (Cloudflare lists 32k — the sources disagree), text only, no per-account
  fine-tuning. Architecture and size undisclosed.
  https://docs.typesafe.ai/models.md
- An unrelated open-weight lookalike, "Kev" (Qwen 3.5, 0.8B/4B/9B), exists.
  https://simonw.substack.com/p/jev-introduces-a-new-shape-of-llm

## How it is used

- Three question shapes: **yes/no** (a probability), **choice** (one of up to
  255 options you supply), **score** (a value on an ordered rubric).
- Build a `state` (the message, context, policy), ask several independent
  questions in parallel, **branch in deterministic code** on the typed
  answers and their probabilities. Zero-shot: labels and criteria are
  natural language in the request.
- $0.042 per million input tokens, output free; 70–500 ms end to end
  (vendor). Claims "193.6x faster, 444.6x cheaper" than frontier LLMs on its
  own workflows, which it concedes are "on the higher end".

## Against a general LLM with a closed-set prompt

- Always a valid label, by construction; an LLM needs schema enforcement and
  a retry.
- Probabilities come with every answer and are claimed to be calibrated —
  usable directly as an escalation threshold. Friday's triage today uses a
  confidence the model reports about itself, against a 0.7 threshold.
- No rationale text.
- Parallel's evaluation (https://parallel.ai/blog/testing-jev): reranking
  comparable to its internal reranker (NDCG@10 ≈ 0.7); **lost on topic
  classification with a large label set** and on freshness classification;
  cost per document "materially higher" than its specialised models.

## Limitations

Text only; weaker on large label sets and unfamiliar tasks; black box; no
public accuracy benchmark with named baselines yet; a week old; an external
API, so messages leave the deployment (the docs say requests are not
trained on, with zero-retention for enterprise).

## Where it could fit Friday

Recorded for the roadmap (`CONTEXT.md` § Project state), not decided:

| Place | Today | With a decision model |
| --- | --- | --- |
| Triage | LLM picks a label, reports its own confidence | `choice` over the enabled types; calibrated probability as the threshold |
| `core:intake` (DESIGN-v2 §10) | agent names types that came close | top-k probabilities |
| Log lines before `Diagnose` | the window's lines go to the model | `score` relevance, keep top N |
| Memory / skill / finding selection | substring match, newest first | rerank candidates |
| Staleness at dispatch (`_overtaken`) | any newer message voids a reply | yes/no: was it already answered? |
| Duplicate tasks | none | yes/no: same incident as task X? |

Preconditions: a `Decider` port in `friday.sdk` (`choose` / `yes_no` /
`score` → value + probability) with an LLM adapter and a Jev adapter, so
triage never calls a vendor directly; the sensitivity policy (DESIGN-v2
§9.2), since room messages would reach a new vendor; plain HTTP rather than
an SDK (few dependencies); and, per `CLAUDE.md` rule 4, a shadow run scored
with `evals/run_triage_eval` — accuracy, confusion matrix, calibration,
latency and cost — before anything switches.
