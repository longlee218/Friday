# 02: Production logs through the devops MCP, reads only

**What to build:** Friday's own client for `devops-generic`, allow-listed to
read tools, and a graph node that finds the reporter's request in Loki.

**Blocked by:** the Keycloak sign-in, and only for the half that needs a
token (2026-09-22). 01's routing half is done.

**Decisions:** D4, D5.

**Status:** mostly done (2026-09-22), and two of its bullets below are
superseded — read this before them.

- **The allow-list is not `allow:` in `config.yaml`.** The operator's call,
  2026-09-21: which tools may be called is declared in code, on the class
  that calls them (`LokiSource.TOOLS`), enforced twice, and an `allow:` key
  in the file is refused at load — `friday/config.py:546`. The bullet below
  asking for a test over the YAML list describes a design that was
  reversed.
- **The auth finding this ticket asked for is written**, and it is the
  answer the ticket allowed for: it cannot be reused without a browser.
  `friday/agent/auth.py` and `authorize.py` carry a one-off interactive
  sign-in that keeps a refresh token at mode 0600 outside the database,
  after which the process only ever refreshes. **Built and tested, not
  committed** — the operator deferred it (2026-09-21).
- **The search order shipped**, and in a stronger form than this bullet:
  the correlationId is pushed into LogQL as `|= "<id>"` so the *read* is
  narrowed rather than the result filtered. Measured: a 400-line read of a
  35-minute window covered 84 seconds and did not contain the request; the
  narrowed read returned it whole in two lines. The endpoint path is the
  fallback when the id matches nothing, and the dossier says so when it
  falls back.
- **The histogram owed below is done**, and `≤ 12 lines` is reached — 8 on
  the real production case.

**What is left:** the not-found `ask(response, time)` and the resume on the
follow-up. There is no `Ask` anywhere in the graph today; a window that
holds nothing returns an `empty` envelope and the run continues.

## What

- `mcp_servers` in `config.yaml` gains `devops` with `allow:` naming exactly
  the read tools in the spec's fact list. `release_*`, `vibecode_*`,
  `godaddy_*_add/edit` never appear. A test reads the allow list and fails on
  any name outside the read set.
- Auth: find where the operator's Keycloak token for this MCP lives on this
  machine and reuse it; if it cannot be reused without a browser, say so in
  the ticket and stop — do not ask the operator to paste a token into `.env`
  without that finding written down.
- Search order: correlationId parsed from `response` → `loki_query_range`
  on `{namespace, app} |= id`; else path + identifier over a window of six
  hours back from the reporter's message. Both bounded by `limit`.
- Not found: the node returns `ask(response, time)`; on the follow-up the
  graph resumes here. Still not found: hand over carrying the window and the
  query.

## Verify

- Scripted transport tests for each search branch; one for the resume.
- A recorded real query against `backend-reelme-v2` in a test marked to skip
  without the MCP.

## Owed by the slice (ticket 00, 2026-09-20)

- **The error-code histogram.** The spec's table gives `FindRequestLog` a
  ceiling of `≤ 12 lines`, reached by the request's own lines plus a
  histogram of ±5 min — counts, not lines. The slice has no histogram: it
  keeps the request's lines, caps loud lines belonging to *other* requests at
  20 and reports the rest as a count. That is the part of the idea a cap can
  do; the histogram is this ticket's.
- **The Loki call has now been made** — see below. Four guesses were right
  and three other things were wrong.


## Measured against the live server, 2026-09-21

The operator reconnected the MCP, so this stopped being a catalogue read and
became a query.

**The guesses that were right.** `loki_query_range` is the tool, and its
arguments are `query`, `start`, `end`, `limit` (plus `direction`). `start`
and `end` take RFC3339, which is what `friday/sources/logs.py` already
sends.

**The three that were wrong, all found by one call:**

1. **The cluster label is `apero_cluster`, not `cluster`.** This Loki
   aggregates every cluster — `loki_label_values` on it returns `byteplus`,
   `oregon`, `oregon-llm`, `oregon-llm-external`, `virginia`, `vultr-ailab`,
   `vultr-external` — so the wrong label name matches nothing at all, and the
   right one is the difference between one product's logs and seven clusters
   merged. The seed rows' value (`oregon-llm`) was right; only the name was
   not.
2. **The answer is a JSON object of streams, one per replica**, not a block
   of text: `{result_type, returned, limit, truncated, streams: [{labels,
   lines}]}`. `splitlines()` on it returned JSON fragments. Streams now merge
   and sort by time — a dossier that interleaves replicas in arrival order is
   one whose surrounding lines belong to a different process than the line
   they surround, and `distil` keeps ±2 lines around what it finds.
3. **Every line arrives as `"<iso> <line>"`**, the same shape `kubectl
   --timestamps` produces. The stamp comes off before the dossier is built.

**`truncated: true` is the server's own word** for "the limit was hit —
narrow the query rather than assuming you saw everything", and it now
reaches `not_checked`. A node reasoning over a sample it believes is
everything is the failure this board keeps finding in new places.

A real answer, trimmed, is the fixture in `tests/test_api_issue.py`
(`LOKI_ANSWER`). A shape this code parses has to be checked against the
thing that produces it.

## What still blocks production, and it is not the query

**Friday cannot connect to this server yet.** Two things, both now
half-done:

- **Transport.** The server is streamable HTTP; `friday/agent/mcp.py`
  handled stdio and SSE only, and an SSE client against an HTTP server fails
  at connect saying nothing about transports. `MCPServerStreamableHttp` is
  wired now, with `transport: http | sse` and `headers:` on an
  `mcp_servers` entry.
- **Authentication.** The server authenticates per person, over OAuth. There
  is no token Friday can present, and **nothing here mints one on purpose**:
  a graph that can obtain its own credential can reach further than the
  operator meant it to. The `config.yaml` block is written out and commented
  out, waiting on a token in `.env` as `DEVOPS_MCP_TOKEN`.

**The allow list is one line long**: `loki_query_range`. This server also
offers `release_apply`, `release_rollback`, `release_rollout`,
`release_set_env`, `godaddy_dns_add_record`, `godaddy_dns_edit_record`,
`vibecode_set_secret`, `vibecode_release` and more that change production.
`allow` is the whole guard — a prompt is a request and a filter is not —
and a name goes on that list when something calls it, never ahead of that.
Ticket 04 will want `release_status` and `release_resolve` for the running
image tag.


## The histogram, 2026-09-21

Built, and it is the reason this check can reach its `≤ 12 lines`.

`Dossier.histogram` counts every error code in the **window** — before the
cut, because counting what survived would be counting the cut — and the
sample of *other* requests' error lines is capped at eight and reported as
"8 of N". The first real run quoted sixty-one lines of another endpoint's
errors and called it a dossier; the same window now reads as eleven lines
and a list of counts.

Surroundings go to **this request's** lines only. Two lines either side of a
sampled error belong to somebody else's story, and they are what pushed the
first version to thirteen lines where the spec allows twelve.

The pattern is `\bERR\d+\b`, a module constant rather than a row: it is an
install's shape rather than a room's, unlike the domain rule the operator
moved into memory. It becomes configuration the day a second stack
disagrees, and a pattern that matches nothing yields an empty histogram,
which is the honest outcome.

**Still this ticket's:** the same-user and same-path fallback, and the Loki
call itself, which waits on the Keycloak client.