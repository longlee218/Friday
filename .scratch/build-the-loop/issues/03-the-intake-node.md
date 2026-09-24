Status: ready-for-agent
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

- [ ] `Intake` makes no model call.
- [ ] Env/service come from the table; a vague request yields the room's
      candidate set, not a guess.
- [ ] `IntakeContext` carries everything the loop's tools need
      (`reported_at`, `container_roots` included).
- [ ] Whole suite green; `code-review` done.
