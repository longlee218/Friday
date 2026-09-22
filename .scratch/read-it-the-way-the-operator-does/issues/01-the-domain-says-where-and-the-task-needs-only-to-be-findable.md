# 01: The domain says where, and a task needs only to be findable

**What to build:** `ApiIssueParams` reshaped to what the operator actually
uses, an environment rule in code, and the channel routing table node 0
looks a domain up in.

**Blocked by:** nothing (2026-09-22). 09 is done, and the routing half
shipped with it — see the amendment at the bottom.

**Revised 2026-09-17:** the routing table is not in a channel context file.
`Resolve` reads `route → service → project` rows (spec, "Memory: one store,
twelve kinds"), and an unknown domain is investigated, not refused
(spec, "Revision 2026-09-17").

**Decisions:** D1, D2, D3.

**Status:** done (2026-09-22). `ApiIssueParams` is `summary, environment,
response, endpoint, identifier, correlation_id, curl`; findability is the
curl **or** the endpoint plus one id, as a `OneOf` group; `correlation_id`
keeps its `doc` and has no `ask`, because it is read out of the response and
never asked for. `staging` is gone from the enum. The environment rule and
the routing half shipped earlier, inside ticket 00's slice.

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


## D1 amended, 2026-09-21 — the environment rule is a row, not code

The operator's call, made while walking through what ticket 00 needs typed
in: **Friday is meant to serve more rooms than one company's**, and a module
naming `aperogroup.ai` is an installation compiled into the system.

The rule was also already wrong about this company. The 2026-09-18 survey in
`research/03-seed-rows.md` found `api-mobile-spec-reviewer.aperogroup.ai`
served from namespace `dev` with no `.dev` in it, and `payment-service` on
both `aperogroup.ai` and `apero.vn` resolving to different AWS endpoints.

**What shipped** (in the ticket 00 slice, since that is what reads it):

- A thirteenth `MemoryKind`, `environment`, data `{suffix, env}`, keyed on
  the suffix, written by the operator and read by code alone.
- `environment_of(domain, rows)` matches by **longest suffix**. The
  convention is two rows; an exception is one more row that wins by being
  longer, with no branch anywhere that knows it is an exception.
- No row matching is `external` — the graph ends promising nothing. **No
  rows at all is a different answer**, and says so: a room that knows nothing
  about any domain is not a room that knows this domain is foreign.
- `_OURS` and `_DEV_LABEL` are gone from `friday/dag/api_issue/resolve.py`.

**What is still this ticket's:** `ApiIssueParams` reshaped to what the
operator actually uses, and the rest of the routing half. `route.env` is
kept alongside `environment` on purpose — the two are typed by hand and
`Resolve` refuses when they disagree rather than picking, since a production
search run against dev is not a thing to discover from its results.
