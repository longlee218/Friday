Status: done
Blocked by:

# The Intake node (deterministic, no LLM)

Decision: [What Intake gathers, and the shape the loop returns](../../the-graph-becomes-a-loop/issues/03-what-intake-gathers-and-the-shape-the-loop-returns.md).
Stub: `../the-graph-becomes-a-loop/intake_and_loop_output_STUB.py`.

## Goal

Build `Intake` — one deterministic node, **no model call** — producing
`IntakeContext`:

- `Placement`: env (domain table), service, clone, repo_path, release_tag,
  namespace, pod_selector, dbs, error_code_doc, **container_roots**. Folds in
  today's `Resolve` (env/service stay a table lookup, never a model guess).
- `Hints`: regex `correlation_id` (uuid) + curl/response artifact ids — no model.
- retrieved, token-bounded, deterministic: `memory`, `skills` (`when:`),
  `related_tasks`.
- `request_text`, `reported_at`.
- `placement_identity = (env, service, clone, repo, tag)`.
- **Service fork (c)→(a) hybrid**: match request against known services/aliases;
  vague → room candidate set, the loop selects by reading. Model never invents a
  service.

## Acceptance

- [x] `Intake` makes no model call. (`intake_node()` takes no agent/harness;
      `node.agent is None` — `test_intake_makes_no_model_call`.)
- [x] Env/service come from the table; a vague request yields the room's
      candidate set, not a guess. (env via `domain_of`/`environment_of`; service
      via the (c)→(a) whole-token match — zero/several matches → candidate set,
      never invention. Tests: resolves-named, vague→candidates, several→candidates,
      more-specific-over-prefix, short-name-not-inside-host.)
- [x] `IntakeContext` carries everything the loop's tools need
      (`reported_at`, `container_roots` included). (`test_intake_context_carries_
      reported_at_and_container_roots_and_hints`.)
- [x] Whole suite green; `code-review` done. (`uv run pytest -q`:
      1624 passed, 1 skipped, 7 failed — the 7 are the pre-existing
      `OPENROUTER_API_KEY`-not-set `ConfigError`s from the operator's uncommitted
      `config.yaml`, unrelated to this change. `code-review` subagent ran:
      no critical/high; M1 + L2 fixed below; two guards deleted-and-watched-red.)

## Built, not wired

The node is on disk (`plugins/devops/graph/intake.py`) and fully unit-tested,
but **not on `build_devops_dag`'s edges** — `prepare`/`resolve`/
`find_request_log`/`read_failing_code` are untouched and still on the live graph.
Putting Intake on the graph and dropping the extractor is **ticket 6**.

## Follow-ups for ticket 6 (from code-review)

- **M1 (fixed here):** service match is now whole-token, not substring, so a
  short name (`api`) cannot resolve inside a host (`api-reelme-v2.dev…`).
  Residual: no **alias table** exists on `devops.service` yet — matching is on
  the canonical `name` only. Add aliases when ticket 6 wires Intake in.
- **L1 (accepted):** `_hints_of` promotes the first *unlabelled* artifact to
  `curl_artifact_id`, so a response pasted without a labelled curl can be tagged
  as the curl. Hints are advisory (the loop reads the real request); revisit if
  the loop relies on the hint.
- **L3 (accepted):** `findings` appear in both `memory` and `related_tasks` (per
  this ticket's spec); `memory` is capped at 20 while `related_tasks` is not, so
  the two can disagree on which findings show when there are >20 non-skill rows.
  Cosmetic — `diagnose_memories` already bounds findings.
- **Placement reconciliation:** this devops-local `Placement` (13 fields,
  `clone_path==repo_path` for now) vs `friday.sdk.sources.Placement` — merge in
  ticket 6. `release_tag`/`dbs` stay defaults until a deterministic source exists.
