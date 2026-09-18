# Contextual Retrieval — recipe and pre-flight checklist

## Pre-flight: should this be reached for at all?

- [ ] Is there an actual corpus, not just a handful of structured records that exact-match/filtered SQL already serves well?
- [ ] Is the total corpus size meaningfully over ~200k tokens (~500 pages)? Under that, putting the whole thing in the prompt with caching may simply be cheaper and simpler than building any retrieval pipeline.
- [ ] Has exact-match or keyword lookup actually been measured as insufficient (a real recall@k number), or is this a hunch that it "probably won't scale"?
- [ ] Would the queries against this corpus benefit from *semantic* similarity (paraphrase, concept match), or are they mostly exact-identifier lookups (IDs, error codes, names) that keyword search already nails?

If the honest answers are "no," "no," "not measured," and "mostly exact," this technique is solving a problem that doesn't exist yet in this system — measure first, build second.

## The pipeline, step by step

```
1. Chunk the corpus
   (size/boundary/overlap: experiment per-corpus, no fixed recipe from the source)

2. For each chunk:
     context_note = llm_call(
       f"<document>{whole_document}</document>\n<chunk>{chunk}</chunk>\n"
       "Give a short succinct context to situate this chunk within the "
       "overall document for the purposes of improving search retrieval "
       "of the chunk. Answer only with the succinct context and nothing else."
     )
     embedded_text = context_note + "\n" + chunk

3. Index embedded_text in BOTH:
     - a vector store (semantic/embedding search)
     - a lexical index, e.g. BM25 (Elasticsearch or equivalent)

4. At query time:
     semantic_hits = vector_search(query)
     lexical_hits  = bm25_search(query)
     fused = weighted_reciprocal_rank_fusion(
         semantic_hits, lexical_hits,
         semantic_weight=0.8, lexical_weight=0.2,   # reference default
     )

5. (Optional) rerank:
     candidates = fused[: k * 10]                    # over-retrieve
     reranked = rerank_model(query, candidates)
     final = reranked[:k]
```

## Example: chunk context, before and after

**Raw chunk** (no context): "The company's revenue grew by 3% over the previous quarter."

**With chunk-specific context prepended**: "This chunk is from an SEC filing on ACME corp's performance in Q2 2023; the previous quarter's revenue was $314 million. The company's revenue grew by 3% over the previous quarter."

The second version is retrievable by a query like "how did ACME's Q2 2023 revenue compare to the prior quarter" — the first version, on its own, has none of the identifying information a query would actually contain.

## Measuring: recall@k, before touching anything

```
baseline_failure_rate = 1 - recall_at_k(
    retrieval_fn = plain_exact_match_or_keyword_search,
    golden_queries = golden_set,               # build this first — real queries,
    k = 20,                                     # known-correct answers
)
# Only after this number exists and is judged too high:
# try each addition (contextual embeddings, then + BM25, then + reranking)
# and re-measure — the value of each layer is precisely the delta it buys
# over the previous measurement, not an assumed improvement.
```

Each layer (contextual embeddings, hybrid BM25, reranking) should be judged by the delta it actually produces against the *measured* baseline for the corpus in question — the source's reported numbers (5.7% → 1.9%) are evidence the technique can work well, not a guarantee it will produce the same gain on a different corpus with different query patterns.
