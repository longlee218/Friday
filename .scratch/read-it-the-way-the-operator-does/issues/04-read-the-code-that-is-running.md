# 04: Read the code that is running, and only read it

**What to build:** The code node — stack frame to file, CodeGraph to follow
it, repo docs read in place, and the running version resolved on production.

**Blocked by:** 01. (Was 01, 02: under v3.2 the running version comes
from `DeploySource`, which this ticket owns, not from the log check.)

**Decisions:** D6, D7.

**Status:** ready-for-agent

## What

- Map `/app/dist/src/x/y.js:80` to `src/x/y.ts` in the repo the table
  names; read the surrounding lines.
- `codegraph explore` as a read tool taking `projectPath` from the table.
- Production: read the pod's image tag through a read tool of the MCP,
  `git worktree add --detach data/checkouts/<repo>@<tag> <tag>` in the
  operator's clone (a worktree is a new directory, not a change to theirs),
  `codegraph init` there. Dev: the main clone as it is. The report records
  which commit was read.
- The repo's `docs/` and `CLAUDE.md` are readable by path; nothing is copied
  into Friday.

## Verify

- A test that the tools in this ticket are all reads: no tool here takes a
  path it writes to except under `data/checkouts/`.
- The frame-to-file mapping tested on the two real frames in the spec.
