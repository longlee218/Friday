Status: ready-for-agent
Blocked by:

# `core.repos`: read, grep, glob over the room's repositories, at the running tag

First of three (operator, 2026-09-30: "tách 3 ticket"), inserted before 15:
23 `core.repos` · [24](24-bash-with-full-rights-and-todo-write.md) `bash` + `todo_write` ·
[25](25-log-tools-named-for-their-source-one-output.md) one log tool per source.
The agents' tools are updated before more actions move onto the spine.
Amends: build-the-spine tickets 08 and 09. Keeps D7.
Source: operator design review, 2026-09-30, after ticket 22; research on
Claude Code's `src/tools/{Glob,Grep,FileRead}Tool` and on Friday's tools.

## The problem

`backend.code` is three special-purpose tools and `backend.docs` a fourth:
`read_code(repo, file, line)` (±15 lines), `search_code(repo, query)`
(`git grep -F`, 40 hits), `what_code_means(repo, code)` (a lookup in
`error_codes_doc`), `read_docs(repo, path, line)`. A model that knows
Claude Code's Read/Grep/Glob has four new shapes to learn, cannot list
files, and cannot search with a regex or context lines. The generic
reading is locked inside one plugin, where the next domain would write it
again.

## What is decided

- **Generic in core, domain knowledge in the plugin.** `read`, `grep`,
  `glob` live in `friday/kernel/toolsets/` as the `core.repos` toolset.
  What a repository is — name, clone path, how to get its running ref, how
  a container path maps into it — is the plugin's, handed to core through an
  SDK contract (`friday/sdk/`, a pure value).
- **D7: read the running version.** At the ref: `git show <ref>:<path>`,
  `git grep` at the ref, `git ls-tree -r` at the ref. No ref (only
  `placement.project` has one today) → the checkout, and the fallback goes
  into `not_checked`, as `read_code` does now.
- **The model names; code resolves.** `repo` is one of the room's
  repositories; a wrong name is refused listing the valid ones
  (`unknown_repo`). A path is relative to the repository; `..` and
  absolute paths outside it are refused.
- **Every read is grounded.** Lines go through `Evidence.show` (L-ids
  `Diagnosis.refs` can cite).
- **Budgets as Claude Code does them** (operator: "làm đúng như
  claude-code-main tools đang làm"): no count of reads. `MAX_READS` and
  `Evidence.spent()` go (every tool that calls them); each tool bounds its
  own output, and the run is bounded by `AgentSpec.budget` alone.
- **Declared explicitly, never from a docstring** (operator, 2026-09-30;
  the rule of [26](26-nothing-a-model-reads-comes-from-a-docstring.md)):
  each tool's description is rendered by a function from the constants it
  enforces and the tool names it points to (Claude Code's `prompt.ts`
  pattern), passed as `description=`; each parameter's description is
  declared beside the parameter, not in `Args:`; semantic checks go in
  `args_validator=` (Claude Code's `validateInput`); the room's repository
  names reach the description per run through `prepare=`. This ticket
  introduces that declaration shape; 26 moves every other tool onto it.

## Goal

| Tool | Input | Output and limits (from Claude Code) |
| --- | --- | --- |
| `read` | `repo`, `path`, `offset=1`, `limit` | `cat -n`-style lines as L-ids. Over 25k tokens or 256 KB without `limit`: **refused**, asking for `offset`/`limit` (an error is cheaper than a truncation). A directory, binary or device path: refused with a hint. Re-reading an unchanged range: a short "unchanged, see L…" stub |
| `grep` | `repo`, `pattern` (regex), `glob?`, `output_mode` = `files_with_matches` (default) \| `content` \| `count`, `-i`, `-A`/`-B`/`-C`, `head_limit=250`, `offset=0` | at the ref; `files_with_matches` sorted, "Found N files"; truncation says `limit/offset`; lines cut at 500 columns; 20k chars; a pattern starting `-` passed with `-e`; VCS dirs excluded |
| `glob` | `repo`, `pattern` | paths at the ref, capped at 100 with a "more specific pattern" note |

- A stack-frame path (`/app/dist/src/x.js:80`) resolves through the
  plugin's mapping (today `repo_file`, `CONTAINER_ROOTS`, and `original`
  for source maps in `plugins/backend/toolsets/code.py`).
- Secret files and dirs (`SECRET_FILES`, `SECRET_DIRS`) are never read or
  matched — a read tool is not the place a secret leaks from, whatever
  `bash` may do (ticket 24).
- `read_code`, `search_code`, `what_code_means`, `read_docs` are deleted;
  `backend.code` and `backend.docs` go, and `trace_problem`,
  `answer_question`, `backend.diagnose` and `backend.explain` name
  `core.repos` instead. `error_codes_doc` and `docs_paths` are named in the
  agents' instructions (or the per-run description) so the model finds them
  with `read`/`grep`.
- Backend's `Placement` exposes the room's repositories through the SDK
  contract; `RunningVersion` stays the plugin's.

## Out of scope

`bash` and `todo_write` (24); `read_log` (25); file tools over SSH; a write
tool of any kind.

## Acceptance

- [ ] `read`/`grep`/`glob` read a room repository at its running tag, fall
      back to the checkout with `not_checked`, show L-ids, and refuse an
      unknown repo, a path outside it, a secret file and an oversize read
      (tests).
- [ ] A container stack-frame path and a `.js.map` frame still resolve
      through `read` (the cases `tests/test_investigate_tools.py` pins).
- [ ] `MAX_READS`/`Evidence.spent()` are gone; each tool's own cap is
      tested.
- [ ] Each description is rendered from its constants, and a test fails
      when a constant changes without the description following.
- [ ] The three tools' definitions are identical with every `__doc__` set
      to `None` (test).
- [ ] `backend.diagnose` and `backend.explain`, run through `run_agent`,
      are offered `read`/`grep`/`glob` and none of the deleted tools (test).
- [ ] Guard tests updated deliberately: `tests/test_tools.py` list,
      `tests/test_core_toolsets.py` count,
      `tests/test_sources_are_the_only_door.py` `ALLOWED`,
      `tests/test_investigate_tools.py`.
- [ ] Agents' instructions name the new tools; `backend.trace_problem` eval
      and `core.planner` run and reported (tool descriptions reach the
      Planner through `agents_allowed`).
- [ ] `docs/DESIGN.md` (toolsets rows, D7 wording), `CONTEXT.md`
      (*Toolset*, new terms), tickets 08/09 amended.
- [ ] Whole suite green; `code-review` done.

## Amended 2026-09-30 (operator, same day): the model finds the tag, not the plugin

The first pass built a `backend.release` toolset (no tools of its own) that
resolved a repo's running tag once per run through `release_status` and
bound it onto a new `Evidence.resolve_ref` hook every `core.repos` call
consulted automatically — kept "D7: read the running version" as something
code did for the model. **The operator rejected this design.** Instead:

- `backend.release`, `RELEASE`, `release_tools`, `RunningVersion`,
  `ReleaseSource`, `Evidence.resolve_ref` and `Evidence.tags` are all
  deleted. There is no plugin-side or kernel-side running-version lookup.
- `read`, `grep` and `glob` each gain their own optional `ref: str | None`
  parameter (a tag, branch or sha), declared the same way as every other
  parameter (`Annotated[..., Field(description=...)]`). With `ref`: `git
  show`/`git grep`/`git ls-tree` at exactly that ref. Without one: the
  checkout, with the fallback said in `not_checked` — D7 still holds, it is
  just the model's tool call that pins the version now, not a per-run cache.
  A `ref` shaped like a flag (`-...`) is refused in `args_validator=`; a
  `ref` git cannot resolve is refused in the tool body (checking it is a git
  call) rather than silently falling back.
- The model is expected to find the tag itself — `release_status` (Helm) or
  a pod's own image tag (k8s) — through devops tools that
  [28](28-diagnose-reads-kubernetes-and-loki-through-devops-generic.md)
  grants; ticket 23 does not grant them, so `backend.diagnose`/
  `backend.explain` currently have no way to *find* a tag, only a place
  (`ref`) to put one once ticket 28 lands. `DIAGNOSE.toolsets`/
  `EXPLAIN.toolsets` name only `core.repos` (plus `backend.logs`/
  `core.memory`/`core.skills` for diagnose) — no `backend.release`.
  `diagnose_prompt.py`/`explain_prompt.py` tell the model to find the
  running tag first and pass it as `ref`, ahead of that toolset existing.
- `docs/DESIGN.md`'s D7 and `CONTEXT.md`'s *Running version* entry are
  rewritten to this shape rather than the rejected one.
