# 27: One place that builds a prompt

**What to build:** Everything an agent should know arrives through one function, so
adding a source of knowledge means changing one place rather than every agent.

**Blocked by:** 24, 25, 26

**Status:** ready-for-agent

There are now several things an agent might need: who it is, what is true of this
channel, what has been learned, which skills exist, and what the conversation has
been about. Left to each agent, that becomes a different subset assembled a different
way in each one, and a new source means visiting all of them.

The order matters as much as the content. What never changes goes first and what
changes most goes last, because a prompt is matched from the front — a byte that
moves early costs a cache hit on everything after it.

- [ ] An agent is given its knowledge by one function rather than assembling it
- [ ] Adding a new source of knowledge changes one place
- [ ] What is stable appears before what changes, so two calls that differ only in the newest message share a prefix
- [ ] An agent can be given a subset, so the most frequently run one does not pay for what it never uses
- [ ] A source that is missing or unreadable degrades to leaving it out, and says so once, rather than failing the run
