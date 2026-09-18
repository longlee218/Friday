# Tool design checklist and examples

## Before adding a new tool

- [ ] Is this wrapping one API endpoint one-to-one, or is it the right *unit of work* for an agent to reach for? (`search_contacts`, not `list_contacts` plus client-side filtering.)
- [ ] Does its name carry namespace/service information the model can use to disambiguate it from similar tools elsewhere? (`asana_projects_search`, not `search`.)
- [ ] Does the description read like you're onboarding a new hire who's never seen this system — explicit vocabulary, query syntax, and relationships between resources, not assumed shared context?
- [ ] Does the response return high-signal, human-readable fields by default, with a way to ask for more detail when it's actually needed — not a firehose of IDs and metadata every caller has to filter down themselves?
- [ ] Does anything that can return a lot of data support pagination, filtering, or range selection, with truncation that tells the agent how to narrow its next call rather than silently dropping results?
- [ ] Do error messages name the specific problem and a corrective action, rather than an opaque code or a raw stack trace?

## Before merging or splitting a set of similar tools

Ask what the tools actually split *by*, not just how many there are:

| Split axis | Consolidate? |
|---|---|
| Different verbs on the same resource, always used in the same order (`create_pr`, `review_pr`, `merge_pr`) | Yes — one tool with an `action` parameter beats three near-duplicates |
| Different verbs the agent picks between based on what it already knows (has a name vs. doesn't; needs metadata vs. needs the full body) | No — this is deliberate cost/knowledge tiering, not duplication |
| Same operation exposed at two different response granularities (full detail vs. summary) | Consolidate into one tool with a response-format parameter, per the `ResponseFormat` pattern below, rather than two separate tools |

## The `ResponseFormat` pattern

```
tool: search_orders
parameters:
  query: string
  response_format: "concise" | "detailed"   # default: concise

concise response:
  order_id, customer_name, status, total

detailed response:
  + line_items, shipping_address, payment_method, audit_trail, ...
```

Concise is the default because most calls only need enough to decide the next step; detailed exists for when the agent has already committed to acting on a specific record. Measured effect in Anthropic's own case: roughly two-thirds fewer tokens on the concise path with no loss of capability, since detailed is always one more call away.

## Description example: before/after the "new hire" test

**Before** (assumes shared context):
> `query_metrics(metric, range)` — Queries the metrics store.

**After** (explicit, onboarding-doc quality):
> `query_metrics(metric: str, range: str)` — Query a time-series metric from the internal metrics store. `metric` must be one of the registered metric names (see `list_metrics` to look them up — don't guess a name). `range` is a duration string like `"1h"`, `"24h"`, `"7d"`; ranges longer than `"30d"` are automatically downsampled to hourly buckets. Returns up to 1000 points; if the range you asked for would return more, the response is truncated to the most recent 1000 and tells you how to split the query into smaller ranges to get the rest.

The second version answers questions a new hire would actually have (where do valid metric names come from? what happens if my range is too long? what happens if there's too much data?) inside the tool definition itself, rather than leaving the agent to guess or fail once and self-correct.

## Iterating like the source describes

1. Prototype the tool (a local MCP server is a fast way to do this: `claude mcp add`).
2. Run it against a handful of realistic, multi-tool tasks — not single-call smoke tests.
3. Read the transcripts for what's *missing* from a response, not just what's wrong in it.
4. Hand the transcripts back to an agent and ask it to propose refinements to the tool's own definition and description — not just to the calling code.
5. Re-run the same tasks against the refined tool before deciding it's better, the same way any other change here would be judged.
