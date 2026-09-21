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

## Owed by the slice (ticket 00, 2026-09-20)

The slice's `read_failing_code` reads **±15 lines around the first frame that
resolves in the clone**, drops `node_modules` and framework frames before
capping at five, and names the frames further down the stack in
`not_checked` rather than opening them.

Still owed here, from the spec's own rule for this check — "±15 lines around
the first frame, **the enclosing function's name, one hop of callers**":

- The enclosing function's name and one hop of callers, which is what
  `codegraph explore` is for and why this ticket owns it.
- The `/app/dist/src/x/y.js:80` → `src/x/y.ts` mapping. The slice strips a
  container root (`/app`, `/usr/src/app`, `/srv/app`) and joins the rest to
  the clone, so a compiled `dist` frame does not reach its TypeScript source.
- The running version. The slice reads HEAD and says so in `not_checked`;
  `DeploySource` and the detached worktree are this ticket's.

## The operator answered finding H, 2026-09-21

Finding H recorded the gap as "image tag → git ref is **assumed, not
known**". The operator's rule, stated while reviewing the first `project`
row: **the image version is the release tag.** So the mapping this ticket
needs is not a guess to be built around — it is a rule, and
`git worktree add --detach data/checkouts/<repo>@<tag> <tag>` can be built
straight on it.

**And the fallback changes with it.** Finding H's remedy was "unknown →
default branch and a `not_checked` line". That is wrong on production, and
the first real `project` row showed why: its `default_branch` is `develop`.
Falling back there would answer a question about production out of the
development branch — code that looks right, is not, and says nothing about
the difference. Logic drift between the two is the ordinary case, not the
exception.

So, on production: **an unresolvable tag reads no code at all** and says so.
No branch fallback. A `not_checked` line saying "I could not tell which
version is running, so I did not read the source" is worth more than forty
lines from a branch nobody deployed.

`default_branch` keeps its job on **dev**, where the main clone is what is
running — which is what the slice already does, and already says in
`not_checked`.


## Done — the frame mapping, 2026-09-21

`/app/dist/src/x/y.js:80` now reaches `src/x/y.ts` **at the line somebody
wrote**, not at line 80 of the build.

**The line is the part that matters, and it is why this is not a path
rewrite.** Measured on the operator's own clone:
`workflow-credit.service.js:60` is `workflow-credit.service.ts:109` —
forty-nine lines away, and both land on the same closing brace. Mapping the
file and carrying the line would have handed `Diagnose` fifteen lines of the
wrong place and called it the throw site: the same shape as reading
yesterday's log window and reporting it as this request's.

`friday/sources/code.py:original` decodes the source map beside the compiled
file — v3, base64 VLQ, every field a delta on the last. No dependency; it is
fifty lines and the format is stable. The clone builds with maps: 1,725 of
them beside `dist/src`, measured the same day.

**No map is "read the built file and say so", never a guess.** A frame whose
map is missing or unreadable is read as it is, with a `not_checked` line
saying it is the built line and not the one you wrote.

### What the review found in the decoder

Three, all of them "answers instead of refusing", which is this board's
recurring shape:

- **A `sources` holding `null`** (legal in v3 beside `sourcesContent`) and a
  source index the deltas ran past the end of both raised out of the graph
  node, past the `try` that wraps the read. The whole walk is inside one now,
  and the contract — `None`, never an exception — holds.
- **An unknown character returned what it had decoded so far.** Every field
  is a delta on the last, so a half-read segment is dropped *with the deltas
  it carried* and every later segment is computed from the wrong base: a
  corrupt map answered with a confident wrong line, which is the one thing
  the function exists to prevent. It rejects the map now.
- **The mapped path was never checked against the clone.** `repo_file`
  refuses a stack frame that climbs out of it and this module's docstring
  makes that the rule — and a `.map` naming `../../../../../../etc/hosts`
  is the same climb by a quieter route, which `original` followed and
  `excerpt` then read. `original` takes the root now, and it is not optional.

**Still this ticket's:** the running image's tag — `release_status` /
`release_resolve` through the devops MCP, which waits on the Keycloak client
— the detached worktree under `data/checkouts/`, CodeGraph, repo docs, and
the enclosing function and caller hop.
