Status: ready-for-agent
Blocked by: 05

# Core Intake and the backend enricher

Decision: [Core intake and the domain enricher](../../domains-plug-in/issues/04-core-intake-and-the-domain-enricher.md).
Stub: `core_intake_and_domain_enricher_STUB.py` on its prototype branch.

## Goal

- `friday/kernel/spine/intake.py`: seed (`request_text` over every reporter
  turn, `reported_at`, raw hints: every uuid, every `ArtifactRef`) → the
  domain's one enricher → retrieve (memory + skills via
  `db.case_memories(channel_id, keys, text)`).
- `plugins/backend/placement.py`: `Placement` = address + domain hints
  (`correlation_id`, `curl_artifact_id`, `response_artifact_id`);
  `IDENTITY = (env, service, clone_path, repo_path)`; `retrieval_keys()`.
  `release_tag` leaves `Placement`. DB only, no network, no model.
- `ops`: no enricher → `domain=None`, identity `()`.
- The live `trace_problem` DAG's intake node calls core `intake()` so the
  code is live, not dead, until 14.
- `diagnose_memories(service=, error_code=, path=)` → `case_memories`;
  `error_code`/`path` go.

## Acceptance

- [ ] Intake makes no model and no network call (test).
- [ ] Named / vague / ops cases produce the stub's three contexts (tests).
- [ ] A reply adding a correlationId leaves identity unchanged; a reply
      naming another env changes it (tests).
- [ ] `CONTEXT.md`: *placement identity* updated (no `release_tag`),
      *retrieval keys*.
- [ ] Whole suite green; `code-review` done.
