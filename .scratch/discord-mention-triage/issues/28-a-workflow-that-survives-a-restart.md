# 28: A workflow that survives a restart

**What to build:** A workflow runs as an ordered set of steps, each taking the state
so far, doing one thing, and handing back what it learned. Killing the process
partway through and starting again continues from the first unfinished step instead
of paying for the finished ones twice.

**Blocked by:** 27

**Status:** ready-for-agent

**This reopens ticket 07, which was closed as superseded and should not have been.**
The reason given there — that a planner writes nothing until it returns, so there is
no position to resume from — is true only while a planner is one cheap call. A step
that spends five tool calls and ten minutes against a log store is a position, and
losing it costs real money.

Building rather than adopting: a graph library was measured at twenty-two extra
packages, a second HTTP client in the same container, and two of its own tables in
the one SQLite file — for a feature that is about a hundred lines here.
**Revisit that when durable resume spreads past two workflows**; below that the
library costs more than it saves, and above it the reverse.

The hard part is neither the state nor the graph. It is that **a step with a side
effect can crash after causing it and before recording that it did** — and resuming
then does it twice. For anything written to this system's own store, the step's
result and its effect commit together or not at all. For anything that has already
left the process, only an idempotency key helps, which is the same choice the outbox
already makes: better to send twice than to lose one.

Branching and running steps side by side are explicitly not the goal. Ordinary Python
already expresses both, and a graph that exists to replace `if` puts a language
between the author and their own code.

- [ ] A workflow is an ordered set of named steps, each declaring what it needs and what it produces
- [ ] Each step's result is recorded before the next begins
- [ ] Killing the process mid-workflow and restarting continues at the first unfinished step
- [ ] A step that writes to this system's own store cannot leave its effect recorded without its result, or the reverse
- [ ] A step that has already sent something outside the process does not send it twice on resume
- [ ] A workflow's state is typed, so a step that reads a value another step never wrote is a mistake that shows up before it runs
- [ ] Adding a workflow adds a workflow, without touching the ones that exist
