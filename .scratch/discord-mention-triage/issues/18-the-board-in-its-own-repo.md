# 18: The board in its own repository

**What to build:** The board becomes a React application in a repository of its own,
showing everything today's board shows. The agent's container still serves it, so
there is still one process, one container, and still nothing listening on a public
address.

**Blocked by:** 17

**Status:** retired — superseded by `.scratch/a-monitor-on-the-whole-path/`

**React 19 + Vite as a static SPA, no meta-framework.** Server-side rendering earns
nothing here — one user, no SEO — and the one thing that would justify a
meta-framework, loading data on the server, is unusable because the data lives in a
Python process. That leaves the choice as "which client renderer", and React wins on
the supply of prebuilt data-display components.

**The bundle travels as a versioned artefact, not as a running service.** The
frontend repo builds and publishes; the agent's image copies the build in, pinned by
version. This is what keeps the split from forcing an authentication scheme into a
tool that deliberately has none: nothing is served from a public origin, and the API
still answers only on loopback.

Two repositories against one API is the failure mode this has to answer for. The
generated client is committed, and regenerating it in CI has to produce no diff —
drift becomes a reviewable file change rather than something a person remembers.
Without that, a renamed field shows up as an empty panel during the incident it was
supposed to help with.

- [ ] The board shows what it shows today: failed sends with their text, tasks by state, the message feed, and the model call behind each message
- [ ] It is a separate repository with its own build, and a developer can work on either half without running the other
- [ ] The agent's container serves the built frontend, and still listens only on loopback
- [ ] The frontend is pinned to a published version rather than built from whatever is on a branch
- [ ] A field renamed in the API fails a check rather than appearing as a blank panel
- [ ] Everything rendered is escaped — every string on this page was written by a stranger in a chat
- [ ] The page still offers no way to change anything


## Retired, 2026-09-14

This ticket asked for the board to move into **its own repository**, published
as a versioned artefact the agent's image copies in. That did not happen, and
it was not forgotten: the board `a-monitor-on-the-whole-path` rebuilt the whole
page as a React + Vite SPA **inside this repository**, in `web/`, and the split
this ticket exists to argue for was never taken up. Three of its seven criteria
are therefore not "still open" — they were decided the other way.

Left `ready-for-agent` long after that, which is the failure `CLAUDE.md` names
about itself one directory over: a reversed decision breaks nothing, so nothing
says it happened.

**Checked against the code that landed, not against the design that replaced
it** — which is the mistake ticket 28's own retirement table recorded making.

| Original criterion | Where it stands |
|---|---|
| Shows failed sends, tasks by state, the feed, the model call behind each message | **Met, and exceeded.** Five screens — Monitor, Board, Rooms, Flow, ContextPanel — where this ticket described one page |
| React, no meta-framework, as a static SPA | **Met.** React 19.2, Vite 7.1, built to static files |
| The agent's container serves it, loopback only | **Met.** `friday/ops/api.py` serves `web/dist` from the same process and refuses a non-loopback bind |
| A renamed API field fails a check rather than blanking a panel | **Met, by a different mechanism.** This ticket wanted a generated client whose regeneration produces no diff; `web/src/api-types.ts` is hand-written and `tests/test_web_contract.py` calls the real converters and asserts the keys they emit. The reason is recorded in `CLAUDE.md`: every route is annotated `-> dict`, so a generated client would pin the routes and none of the fields |
| Its own repository, either half workable alone | **Reversed.** One repo, one process, one container |
| Pinned to a published frontend version | **Reversed.** Built from source here; there is no artefact to pin |
| The page offers no way to change anything | **Reversed.** Four write routes exist — rename a room, write a channel's context, set overrides, reload context — and they are what the operator actually uses |

**One criterion is met by construction and has no test**, which is worth
leaving written down rather than quietly counting as done: "everything rendered
is escaped". No `dangerouslySetInnerHTML` appears anywhere in `web/src`, so
React escapes every string — but nothing asserts that it stays that way, and
every string on these pages was typed by a stranger in a chat. That is a guard
somebody should write; it is not this ticket's to carry into retirement.

**If the split is ever wanted again**, the argument in this ticket still holds
on its own terms and the thing to re-read is the paragraph on two repositories
against one API. What changed is not that the argument was wrong — it is that
nobody needed the split, and a second repository has a cost that is paid daily.
