# Claude RAG Cookbook / Contextual Retrieval — Anthropic

Anthropic's own writeup and reference implementation of "Contextual Retrieval," a
technique that reduces retrieval failures in RAG pipelines by prepending
chunk-specific, LLM-generated context to each chunk before it is embedded and
indexed.

- Engineering blog post: <https://www.anthropic.com/engineering/contextual-retrieval> (2024-09-19)
- Reference implementation notebook: <https://github.com/anthropics/claude-cookbooks/blob/main/capabilities/contextual-embeddings/guide.ipynb>
- Baseline RAG notebook (summary indexing + reranking, no contextual step): <https://github.com/anthropics/claude-cookbooks/tree/main/capabilities/retrieval_augmented_generation>
- Evaluation harness for the baseline notebook: `capabilities/retrieval_augmented_generation/evaluation/` (Promptfoo-based)

## Key techniques found

**Chunking.** The blog post is deliberately non-prescriptive — "chunk size,
chunk boundary, and chunk overlap... all affect retrieval performance," and it
tells readers to experiment. The reference notebook doesn't implement a
chunker at all: it works from a pre-chunked dataset (`data/codebase_chunks.json`,
737 chunks pulled from 9 codebases via "basic character splitting"). Chunking
is explicitly out of scope for what the notebook demonstrates — the technique
is about what happens *after* chunking, not the chunking itself.

**Contextual embeddings.** For each chunk, Claude (the notebook uses
`claude-haiku-4-5`, `temperature=0.0`) is given the *whole document* wrapped in
`<document>` tags plus the individual chunk in `<chunk>` tags, and asked to
"give a short succinct context to situate this chunk within the overall
document for the purposes of improving search retrieval of the chunk. Answer
only with the succinct context and nothing else." The blog post's own example:
a chunk reading "The company's revenue grew by 3% over the previous quarter"
becomes "This chunk is from an SEC filing on ACME corp's performance in Q2
2023; the previous quarter's revenue was $314 million. The company's revenue
grew by 3% over the previous quarter." That ~50–100 token prefix is prepended
before the chunk is embedded (the notebook uses Voyage AI's `voyage-2`, batched
128 at a time) — not stored as separate metadata, but literally part of the
text that gets embedded.

**Contextual BM25 + hybrid search.** The same contextualized text also goes
into a BM25 (lexical/sparse) index — the notebook builds this in Elasticsearch,
with the English analyzer and BM25 similarity over both `content` and
`contextualized_content` fields. Retrieval runs both the semantic (embedding)
and lexical (BM25) searches independently, then fuses the two ranked lists
with weighted Reciprocal Rank Fusion — `retrieve_advanced()` in the notebook
defaults to 80% semantic / 20% BM25, scoring each candidate as
`semantic_weight * (1/(rank+1)) + bm25_weight * (1/(rank+1))` and re-sorting on
the combined score. The rationale in the post: embeddings alone under-perform
on exact identifiers — error codes, function names, correlation IDs — that a
lexical match catches directly.

**Reranking.** An optional last stage: over-retrieve (the post uses top-150,
the notebook over-retrieves 10x the target k), pass query + candidates through
Cohere's `rerank-english-v3.0`, and keep only the top-k the reranker actually
scores as relevant. The post is explicit that this is a latency/cost tradeoff,
not a free win — it costs an extra network round-trip per query in exchange for
sending fewer, better chunks to the generation call.

**Evaluation method.** The metric is `1 - recall@k`, called out at k=20, k=10
and k=5 — i.e., what fraction of queries fail to retrieve their gold chunk
within the top-k. The notebook's own evaluation section names this "Pass@k":
did the golden chunk appear in the top-k retrieved, checked by matching
retrieved chunk content against known-golden chunk content, per domain
(codebases, fiction, ArXiv papers, science papers). The post reports
compounding gains against a baseline failure rate of 5.7% (Gemini
`text-embedding-004`, top-20, no contextual step): contextual embeddings alone
→ 3.7% (−35%); contextual embeddings + contextual BM25 → 2.9% (−49%);
+ reranking → 1.9% (−67%). The post is upfront that a generic document summary
prepended to chunks (instead of a chunk-specific one) gave "very limited
gains" — the technique's value is specifically in the context being
chunk-specific, not just "more context."

**Cost note.** Generating the per-chunk context is a one-time preprocessing
cost, made cheap by prompt caching — the post quotes $1.02 per million document
tokens with caching enabled. The post also states its own limit case: under
~200k tokens of total knowledge base (~500 pages), just putting the whole
corpus in the prompt with caching may beat building a RAG pipeline at all.

## Related primary sources found

- Contextual Retrieval post: <https://www.anthropic.com/engineering/contextual-retrieval>
- Effective context engineering for AI agents (Anthropic engineering, adjacent piece on context management generally, not RAG-specific): <https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>
- `claude-cookbooks` repo root: <https://github.com/anthropics/claude-cookbooks>
- Contextual embeddings notebook: <https://github.com/anthropics/claude-cookbooks/blob/main/capabilities/contextual-embeddings/guide.ipynb>
- Baseline RAG notebook + its Promptfoo evaluation harness: <https://github.com/anthropics/claude-cookbooks/tree/main/capabilities/retrieval_augmented_generation>

## Relevance to Friday

Mostly, it doesn't apply, and it's worth saying so plainly rather than
stretching for a fit. Friday has no document corpus, no vector search, and no
embeddings anywhere in the codebase. What it calls "memory"
(`friday/kernel/memory/`) is a SQLite table of short structured rows in five kinds
(fact/constraint/finding/decision/voice), retrieved by exact filters —
channel, task, agent, source-message identity carried on the read-only
`FridayState` — through `friday/kernel/tools/memory.py`'s `memory_search`/
`memory_add`/etc. That's the opposite of what Contextual Retrieval solves: it
assumes a corpus large enough that similarity search beats reading everything,
and Friday's per-channel memory is capped at 200 rows (`D18`) specifically
because that's still small enough for exact scoping to work well. Likewise,
verbatim material a reporter sends (code, stack traces, curl commands) is
stored whole as its own `Artifact` row and referenced by a content-blind,
generated description (kind + size, never a snippet of the bytes) — the
opposite of embedding the actual content for retrieval, and deliberately so
(the description is shown to the summariser, and the post's own chunk-context
idea of "explain what's in this thing" is exactly what Friday's design
forbids here, precisely because doing it from the content risks leaking it).

Where it plausibly *would* apply, if the system grows: the `voice` and domain
memory kinds are the one place Friday already has a cap that exact-match
scoping was chosen deliberately to stay under (200/channel) — if that ceiling
were ever raised because exact filtering stopped being precise enough (e.g.
many similar-but-not-identical `finding` rows for one channel), that's the
first candidate for embedding-based retrieval, and it would need the
notebook's own recall@k evaluation run against Friday's actual memory rows
before adopting it, not assumed. The `.scratch/read-it-the-way-the-operator-does/`
investigation work (Diagnose/Collector graph for `api_issue`) was the other
candidate worth checking, but its own spec describes deterministic,
code-driven steps — domain-name rules, pod-name lookup, correlationId grep —
not semantic search over a corpus of past incidents; nothing in that spec
currently calls for RAG either. The honest takeaway is: before reaching for
this technique anywhere in Friday, the cookbook's own method says to first
measure — build a small golden query/answer set against whatever corpus is in
question and compute recall@k with plain exact-match retrieval, the same way
the post computed its 5.7% baseline before trying anything cleverer. If plain
lookup already clears the bar the way it does today for memory and artifacts,
contextual retrieval is solving a problem Friday doesn't have yet.
