---
name: claude-rag-cookbook
description: Decide whether retrieval-augmented generation is even the right tool before reaching for it, and if so apply Contextual Retrieval — chunk-specific context prepended before embedding, hybrid BM25-plus-semantic search, reranking, and recall@k evaluation. Use this when a corpus or memory store is growing past what exact-match or keyword lookup handles well, when evaluating retrieval quality, or when someone proposes adding embeddings/vector search (including revisiting Friday's exact-match SQL memory or artifact storage) without first measuring a baseline against it.
---

# Contextual Retrieval (the Claude RAG Cookbook)

Anthropic's technique and reference implementation for reducing retrieval failures in RAG pipelines (`anthropic.com/engineering/contextual-retrieval`), by prepending chunk-specific, LLM-generated context to each chunk before it's embedded and indexed. Reference notebook: `anthropics/claude-cookbooks`, `capabilities/contextual-embeddings/`.

## Read this section before any of the technique below

Contextual Retrieval solves a specific problem: *a corpus large enough that similarity search beats reading everything*, where plain chunking loses cross-chunk context a query needs. It is not a default upgrade for "we have some stored data now." The source names its own limit case directly: **under roughly 200k tokens of total knowledge base (~500 pages), just putting the whole corpus in the prompt with caching may beat building a RAG pipeline at all.** Check the actual corpus size and whether plain lookup is already failing before reading further — the technique is worthless applied to a problem that doesn't exist yet.

## The technique, in order

1. **Chunking** — deliberately out of scope for the source's own guidance; chunk size, boundary, and overlap all affect results, and the recommendation is to experiment rather than adopt a fixed recipe. The reference notebook works from a pre-chunked dataset and doesn't implement a chunker at all.
2. **Contextual embeddings** — for each chunk, an LLM is given the *whole document* plus that one chunk, and asked to write a short (~50–100 token) note situating the chunk within the document, for the purpose of improving retrieval. That note is prepended to the chunk text **before** embedding — it becomes part of what gets embedded, not separate metadata alongside it. A generic, non-chunk-specific summary prepended the same way gave "very limited gains" in the source's own testing — the value is specifically in the context being chunk-specific.
3. **Contextual BM25 + hybrid search** — the same contextualized text also feeds a lexical (BM25) index, run alongside the semantic one. The two ranked lists are fused with weighted reciprocal rank fusion (the reference defaults to 80% semantic / 20% lexical). The reason to keep the lexical half at all: embeddings alone under-perform on exact identifiers — error codes, function names, correlation IDs — that a keyword match catches directly and a semantic one can miss.
4. **Reranking** (optional) — over-retrieve, then rerank the candidates with a dedicated reranking model, keeping only the top-k it scores as actually relevant. This is a real latency/cost trade, not a free win: an extra network round trip per query, in exchange for sending fewer, better chunks to the generation call.

Measured, compounding gains against a baseline failure rate of 5.7% (no contextual step): contextual embeddings alone → 3.7%; + contextual BM25 → 2.9%; + reranking → 1.9%. Generating the per-chunk context is a one-time preprocessing cost, made cheap by prompt caching.

## Evaluate with recall@k — before and after

The metric: `1 - recall@k` — what fraction of queries fail to retrieve their known-correct chunk within the top-k results, checked at k=20/10/5. **Measure this with plain exact-match or keyword retrieval first, as the baseline**, the same way the source computed its 5.7% starting point before trying anything cleverer. If plain lookup already clears the bar for the corpus in question, contextual retrieval is solving a problem that doesn't exist there yet — don't adopt machinery to fix a number you haven't measured.

Step-by-step recipe and a "should we even do this" checklist live in [`references/contextual-retrieval-recipe.md`](references/contextual-retrieval-recipe.md). Full citations: `docs/research/agentic-system-design/claude-rag-cookbook.md`.

## Applying this to Friday

**This mostly doesn't apply today, and that's worth saying plainly rather than stretching for a fit.** Friday has no document corpus, no embeddings, and no vector search anywhere in the codebase. Its "memory" (`friday/memory/`) is a SQLite table of short structured rows, retrieved by exact filters (channel/task/agent/source-message identity) — the opposite of what Contextual Retrieval solves, and deliberately so: the per-channel memory cap (200 rows) was chosen specifically because that scale is still small enough for exact scoping to work well. Verbatim reporter material (code, stack traces, curl commands) is likewise stored whole and referenced by a content-blind, generated description — never embedded — precisely to avoid the content-leaking failure the source's own "explain what's in this chunk" step would otherwise risk.

**The one place worth watching**: if the per-channel memory cap were ever raised because exact filtering stopped being precise enough (many similar-but-not-identical `finding` rows crowding one channel), that becomes the first real candidate for embedding-based retrieval in this system — and the correct first step, per this source's own method, is building a small golden query set and measuring exact-match recall@k *before* adopting anything, not assuming the cap has been outgrown. The Diagnose/Collector investigation redesign on `.scratch/read-it-the-way-the-operator-does/` was checked as a second candidate and doesn't fit either — its steps are deterministic and code-driven (domain-name rules, pod lookup, correlationId grep), not semantic search over a corpus of past incidents.
