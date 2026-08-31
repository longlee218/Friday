# 18: The board in its own repository

**What to build:** The board becomes a React application in a repository of its own,
showing everything today's board shows. The agent's container still serves it, so
there is still one process, one container, and still nothing listening on a public
address.

**Blocked by:** 17

**Status:** ready-for-agent

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
