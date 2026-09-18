# 02: Production logs through the devops MCP, reads only

**What to build:** Friday's own client for `devops-generic`, allow-listed to
read tools, and a graph node that finds the reporter's request in Loki.

**Blocked by:** 01.

**Decisions:** D4, D5.

**Status:** ready-for-agent

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
