# 23: One seam for the agent SDK

**What to build:** Replacing the agent SDK becomes a rewrite of one module rather
than of every agent. Nothing behaves differently; what changes is that exactly one
place knows which library runs a model.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

The SDK is in this project for speed, not for keeps. That is a decision, and it only
holds if the seam is real: four modules import `agents` today, and the fourth is the
one that matters — declaring a tool pulls the library in, so *every* agent written
from here on inherits the dependency.

Bundled with it, because it is the same kind of tidying and cheaper done once: the
call record is the only domain type that does not live with the others. The store
imports the logging module to get a type, which is an arrow pointing the wrong way.

- [ ] One module imports the agent SDK; a new agent can declare instructions, tools and a context type without naming the library
- [ ] Declaring a tool does not require importing the SDK
- [ ] The logging hooks are reached through the same seam rather than the library directly
- [ ] Every domain type lives with the other domain types, and the store does not import a logging module to find one
- [ ] Both existing agents behave exactly as before, proven by their tests passing unchanged
- [ ] A test fails if a second module reaches for the SDK directly
