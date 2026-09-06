# 05: `web/` exists, and one image builds both halves

**What to build:** A React + Vite app at `web/`, served as static files by the
existing FastAPI app, built by a Node stage in the one Dockerfile, with a
TypeScript client generated from the API's own OpenAPI schema.

**Blocked by:** 01

**Decisions:** D2, D3, D4, D11

**Status:** todo

## Why

**React 19 + Vite, no meta-framework (D3).** Ticket 18 of
`discord-mention-triage` already argued this and the argument is better than
restating it: *"Server-side rendering earns nothing here — one user, no SEO —
and the one thing that would justify a meta-framework, loading data on the
server, is unusable because the data lives in a Python process."*

**In this repo, not its own (D2).** This reverses ticket 18's *"The frontend
repo builds and publishes."* Ticket 18's reason for the split is *"what keeps
the split from forcing an authentication scheme into a tool that deliberately
has none"* — and that argument protects the static pinned bundle, not the
second repository. A `web/` directory built to static files and served by the
same app forces no authentication either. Two repos, a publish pipeline and a
version bump per UI change is a cost paid in advance against a problem that has
not appeared.

**One Dockerfile, and `dist/` is never committed (D4).** The Dockerfile is
already multi-stage (`uv` build → slim runtime) and its opening comment is the
architecture's first constraint: *"One process, one container."* A Node stage
builds `web/`; the runtime stage copies the output and contains no Node. A
committed `dist/` would let source and build disagree with nobody noticing —
the exact failure ticket 18 wrote its "regenerating the client must produce no
diff" criterion against.

**The client is generated, and a Python test fails when it drifts (D11).**
FastAPI already serves `/openapi.json`, and `tests/test_api.py` already has
`test_a_typescript_client_can_be_generated_from_it` — which today asserts only
that the schema mentions `/api/board`. Nothing generates anything. The
expensive failure for a read-only SPA is not a misplaced button; it is a
renamed field rendering `undefined` in silence. That is what the drift test
catches, and it runs inside `uv run pytest -q` rather than a second test
runner.

## Acceptance criteria

- [ ] `web/` builds to static files with `npm run build`, and the app runs
      against the live API in development (`board_origins` in `config.yaml` is
      the CORS hook and is currently `[]`)
- [ ] FastAPI serves the built bundle, with client-side routes falling back to
      `index.html` rather than 404-ing
- [ ] One Dockerfile, one `docker compose up`, no Node in the runtime image —
      check by looking for `node` in the final stage, not by assuming
- [ ] `web/dist/` and `node_modules/` are both in `.gitignore`
- [ ] A TypeScript client is generated from `/openapi.json` and committed, and
      a test fails when regenerating it would produce a diff
- [ ] The generation command is written down in `CLAUDE.md` next to the other
      `uv run` commands — an undocumented codegen step is one nobody reruns
- [ ] `CLAUDE.md`'s layout table gains `web/`, and its Environment section
      gains whatever `npm` commands a person needs
- [ ] The suite passes; `pyproject.toml` gains no JS-related dependency

## Notes

No frontend tests (D11). Recorded in the spec as a decision so it is not read
later as an oversight.

Design work is not freehand: the UI is built through the `ui-ux-pro-max` skill,
whose rules are the floor — contrast 4.5:1, visible focus rings, SVG icons
rather than emoji, real motion timing, no gray-on-gray. This ticket is the
scaffold; the three screens (06, 07, 08) are where that applies.

Ticket 19 of `discord-mention-triage` (Liquid Glass) and ticket 20 (SSE) stay
open and out of scope. The SPA polls, exactly as ticket 20's own escape hatch
allows: *"Polling already works… if it turns out not to be small, the honest
outcome is to stop and keep polling."*
