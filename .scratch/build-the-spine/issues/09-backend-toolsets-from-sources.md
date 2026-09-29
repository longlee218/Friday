Status: done (acceptance box 4 half-open: eval waits on a model key)
Blocked by: 05, 07

# Backend toolsets from `sources/`

Decisions: [The target module layout](../../domains-plug-in/issues/09-the-target-module-layout.md) §3,
[The plugin API surface](../../domains-plug-in/issues/03-the-plugin-api-surface.md) §2,
[Core intake](../../domains-plug-in/issues/04-core-intake-and-the-domain-enricher.md) §6,
[Designing answer_question](../../domains-plug-in/issues/06-designing-answer-question.md) §5.

## Goal

`plugins/backend/sources/` folds into `plugins/backend/toolsets/`, one file
per data source holding tools + client: `logs.py` (`backend.logs`:
`read_log`), `code.py` (`backend.code`: `read_code`, `what_code_means`,
`search_code` new; every tool takes `repo`), `docs.py` (`backend.docs`:
`read_docs` under `docs_paths`), `db.py` (`backend.db`), `release.py`.

- Each `ToolsetSpec` declares `mcp={server: Source.TOOLS}`; the factory gets
  `Reads` narrowed to that toolset from `RunContext`.
- The running version: a wrapped devops-generic read returns only the tag;
  code is read at it with `git show <tag>:<file>`, never a checkout.
  (Since 07 `read_code` reads the clone's checkout; `at_ref` waits here.)
- `ssh_host`, `loki_server/tool`, `container_roots`, `not_ours` become
  constants in `plugins/backend`; `DevopsConfig`/`load_devops_config` and the
  plugin's config block go.
- `ApiIssueDeps` dissolves into the factories. The live DAG uses the new
  toolsets until 14.

## Acceptance

- [x] `tests/test_sources_are_the_only_door.py` allow-list =
      `plugins/*/toolsets/` + `core.shell`; watched red.
- [x] A factory cannot reach `release_rollback` (test on narrowed `Reads`).
- [x] `read_code` reads at the running tag, not the working copy (test).
- [ ] Replay via `CannedReads` still passes; `run_api_issue_eval` numbers
      reported unchanged. — **Half done (2026-09-29).** Replay passes: the
      CannedReads tests, `replay_case.py --case data/cases/prod-onboarding-400.json`
      (no model) identical to HEAD, and the case's canned Loki answer read
      through the new `read_log` holds the decisive line. The eval needs a
      model and there is no OpenRouter key: not run.
- [x] `docs/DESIGN.md` "Three layers" corrected (sources folded).
- [x] Whole suite green; `code-review` done.

## Built (2026-09-29)

Operator's calls this session: (Q1) the room's projects ride on
`Placement.projects` (outside `IDENTITY`), `RunContext` gains `reported_at`;
(Q2) `read_code`/`search_code`/`read_docs` resolve the running tag themselves
(`RunningVersion`, cached per run in `Evidence.tags`) and fall back to the
checkout saying why; (Q3) `backend.db` (`describe_db`, `query_db`) registered,
granted to no action.

Left open, on purpose:
- `release_status(project=)` is assumed to take the **service name** — not
  measured. Wrong means every read falls back to the checkout, visibly.
- Only the case's own repo has a running tag. `answer_question`'s "prod when
  none" (map ticket 06 §6) needs a repo → service mapping → ticket 15.
- `not_ours` was never read by any code; dropped rather than made a constant.
- `Plugin.config` (sdk field + `plugin_host` branch) stays; no plugin sets it.
  Its deletion (map ticket 03 amendment §2) is not in this ticket's wording.
- `BootContext.build_tools` is the DAG's bridge to the core narrowing; goes
  with `caps` (14/16). `toolsets/logs.py` imports `graph/distil.py`; it moves
  when the graph is deleted (16). Duplicate `_rfc3339`/`_text_of` in
  `logs.py` predate this ticket.
