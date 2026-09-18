# 01: The domain says where, and a task needs only to be findable

**What to build:** `ApiIssueParams` reshaped to what the operator actually
uses, an environment rule in code, and the channel routing table node 0
looks a domain up in.

**Blocked by:** 09 for the routing half; the `ApiIssueParams` half is free.

**Revised 2026-09-17:** the routing table is not in a channel context file.
`Resolve` reads `route → service → project` rows (spec, "Memory: one store,
twelve kinds"), and an unknown domain is investigated, not refused
(spec, "Revision 2026-09-17").

**Decisions:** D1, D2, D3.

**Status:** ready-for-agent

## Why

The current shape says "correlationId or curl makes a request findable" and
asks the reporter for "the correlationId". The operator never gets one from
a reporter — they get it from the *response* the reporter pastes — and a
reporter who wrote "login API, deviceId X, 500" has given enough without
either. The enum lists `staging`, which no project has.

## What

- `environment` ∈ {`dev`, `production`}; `external` is a *route* outcome,
  not a value the reporter names.
- Fields: `curl` (verbatim), `response` (verbatim, source of correlationId),
  `endpoint`, `identifier` (deviceId / userId / email / orderId as the
  reporter wrote it), `summary`. Findability rule: `curl`, or `endpoint` +
  `identifier`. `ask` for `response` reads "the response you got back".
- Environment from the curl's domain by `D1`'s rule, in code, before any
  model call; only asked when there is no curl.
- Routing table in the channel context, structured: domain →
  `{env, cluster, namespace, app}` or `{env, pod_pattern}`, and `app →
  repo_path`. A reader function, a shape test, and a hand-over reason for a
  domain with no row.

## Verify

- Tests for the rule on the three example domains and an external one.
- `test_web_contract` and the extractor's schema test still green.
- The `ask` phrases: `test_which_questions_this_rule_binds_is_derived_not_
  counted` still pins the count after the change.
