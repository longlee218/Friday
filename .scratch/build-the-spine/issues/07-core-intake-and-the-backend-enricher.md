Status: done
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

- [x] Intake makes no model and no network call (test).
- [x] Named / vague / ops cases produce the stub's three contexts (tests).
- [x] A reply adding a correlationId leaves identity unchanged; a reply
      naming another env changes it (tests).
- [x] `CONTEXT.md`: *placement identity* updated (no `release_tag`),
      *retrieval keys*.
- [x] Whole suite green; `code-review` done.

## Notes (2026-09-29)

- Operator calls during the build: `SkillWhen.services` → `service`, so a
  retrieval key matches the `when` list of the same name exactly (a finding
  matches when its data carries every key); the env comes from the **newest
  turn** with a URL (`db.original_turns_for`), so a reply pasting a prod URL
  after a dev one changes the identity.
- `IntakeContext.related_tasks` dropped: nothing read it, findings arrive in
  `memory`.
- `container_roots` stays on `Placement`, filled by the DAG node (config the
  DB-only enricher cannot read) until 09 makes it a constant.
- Carried to 09: `read_code` reads the clone's checkout now that
  `release_tag` is gone; `sources/code.py:at_ref` and `CodeSource.at_ref` are
  kept, without a production caller, for 09's tag read.
- `SkillWhen.error_codes`/`path_patterns` are no longer matched (no domain
  key names them); left in the schema.
