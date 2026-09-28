Status: ready-for-agent
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
- `ssh_host`, `loki_server/tool`, `container_roots`, `not_ours` become
  constants in `plugins/backend`; `DevopsConfig`/`load_devops_config` and the
  plugin's config block go.
- `ApiIssueDeps` dissolves into the factories. The live DAG uses the new
  toolsets until 14.

## Acceptance

- [ ] `tests/test_sources_are_the_only_door.py` allow-list =
      `plugins/*/toolsets/` + `core.shell`; watched red.
- [ ] A factory cannot reach `release_rollback` (test on narrowed `Reads`).
- [ ] `read_code` reads at the running tag, not the working copy (test).
- [ ] Replay via `CannedReads` still passes; `run_api_issue_eval` numbers
      reported unchanged.
- [ ] `docs/DESIGN.md` "Three layers" corrected (sources folded).
- [ ] Whole suite green; `code-review` done.
