# 21: Look up a trace

**What to build:** A report that arrives with a correlationId or a curl comes back
with what the logs actually say about it. Nobody is answered yet — the finding is
attached to the task and visible on the board, which is enough to tell whether the
lookup is worth trusting before anything is said out loud on the strength of it.

**Blocked by:** 12, 14

**Status:** ready-for-agent

This is the missing half of the system. `plan_api_issue` parks a traceable report
with the reason *"has enough to trace"* — and then nothing traces it, because
tracing does not exist. Every criterion about answering, approving, and posting a
reply is built and unexercised for want of this one thing.

**Fetching is deterministic; reading is not.** A query for a correlationId is a
lookup with one right answer. Deciding what a hundred log lines mean is judgement,
and that is where the `api_issue` workflow becomes agentic — the promotion the
design always anticipated, on evidence rather than on principle.

Two things have to be settled before this can start, and neither can be guessed:
how Loki is reached, and what a correlationId *is* in the log schema — a label, or
a field inside the line. The second decides whether a lookup is a cheap indexed
filter or a scan, and therefore whether this is affordable at all.

**Volume is the real risk.** Logs are unbounded and a model's context is not. A
query that returns everything for a busy service will cost more than the answer is
worth and drown the signal it was looking for. What comes back has to be bounded
before it is read, and the bound has to be visible when it bites.

- [ ] A task carrying a correlationId or a curl is looked up without anyone asking
- [ ] What the logs said is attached to the task and readable on the board
- [ ] A lookup that finds nothing says so, and the task goes to a human rather than being quietly abandoned
- [ ] A lookup that fails — unreachable, unauthorised, timed out — becomes work for a human, never silence
- [ ] What comes back is bounded by time and by size, and being truncated is visible rather than silent
- [ ] Credentials for the log store live with the other secrets and never reach a log, a prompt, or the database
- [ ] A trace is read-only, and nothing about it can post, change or delete anything
- [ ] The lookup is exercised in tests without reaching the real log store
