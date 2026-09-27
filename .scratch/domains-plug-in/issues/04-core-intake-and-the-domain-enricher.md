Type: prototype
Status: resolved
Blocked by:

# Core intake and the domain enricher

## Question

Stub the split of today's `plugins/devops/graph/intake.py`: the **core intake
context** every action gets (request text, reported-at, uuid/artifact hints,
memory/skill retrieval) and the **domain enrichment** (backend: `Placement` from
the environment/service/project rows). Where `placement_identity` is declared per
domain, what `ops.request_permission` gets (no placement), and how the enriched
context reaches the action's agent. Must keep the reply-resume path working
(intake re-runs every pass over the whole conversation).

## Answer

Resolved 2026-09-28 (prototype + grilling). Prototype: branch
`prototype/core-intake-and-domain-enricher`, file
`.scratch/domains-plug-in/core_intake_and_domain_enricher_STUB.py` — run it to
see three cases (named, vague, ops) and the identity diff on reply passes.

1. **Core `intake()` runs three steps for every action**: *seed* (core:
   `request_text` from `original_text_for` — every reporter turn, replies
   included — `reported_at`, regex hints) → *enrich* (the domain's enricher)
   → *retrieve* (core: memory + skills). Retrieval rules are written once, in
   the core.
2. **Hints are raw**: core parses the store's own formats once — every uuid,
   every `[artifact id: description]` as an `ArtifactRef`. What they *mean*
   (the curl, the correlationId, the response) is the domain's call.
3. **One enricher per domain, returning one type** — no wrapper beside it.
   Backend's is `Placement`, now holding the address *and* the domain's hints
   (`correlation_id`, `curl_artifact_id`, `response_artifact_id`). Identity is
   a declared field subset on that type (`IDENTITY`); hints are outside it, so
   a reply adding a correlationId continues. Live per-run handles (`deps`)
   stay separate — ticket 03. A domain with no enricher (`ops`) gets
   `domain=None`, identity `()`: a reply always continues. Name kept:
   `Placement`.
4. **`IntakeContext` reaches every agent in the run, typed.** Core renders the
   common sections (request, memory, skills, reported-at); the domain's agents
   render `ctx.domain` themselves and its toolset factories take the
   `Placement` typed. Boot refuses an agent/toolset that needs a domain type
   granted to an action whose domain's enricher returns another.
5. **`retrieval_keys()`** on the domain type returns named keys
   (`{"service": …}`), matched against the runbook `when` field of the same
   name. Only core `intake()` calls it, then `db.case_memories(channel_id,
   keys, text)` — replacing `diagnose_memories(service=, error_code=, path=)`;
   `error_code`/`path` (no production caller) go. What Intake cannot know yet
   the agent fetches just-in-time through `core.memory`.
6. **The enricher reads the DB only** — no model, no network — so re-running
   it every reply pass stays cheap and the diff stable. **`release_tag` leaves
   `Placement` and the identity** (it was never filled: `ReleaseSource` is
   built into `deps` but nothing calls it, so `read_code` silently reads the
   working copy). Identity is now `env, service, clone_path, repo_path`. The
   diagnose agent learns the running version itself through a wrapped
   devops-generic read and reads code at that tag (`git show <tag>:<file>`,
   never a checkout); its instructions describe the order.

Build consequences: `placement_identity` moves off `IntakeContext` onto the
domain type; `intake.py`'s `_hints_of` / `_placement` split between core and
`plugins/backend`; acknowledge / report / diagnose read `ctx.domain` instead
of `intake_of(state["intake"])`. Carried to the plugin-API ticket: the
MCP-wrapping tools and their read-only allowlist.
