# 07: Multi-node procedures with resume

**What to build:** A task runs an ordered sequence of reasoning steps, and interrupting
the service mid-sequence resumes where it left off instead of starting over. Proven
against a stand-in sequence, since the real ones are not yet specified.

**Blocked by:** 04, 05

**Status:** ready-for-agent

- [ ] A stand-in two-step sequence runs to completion and records each step's result before the next begins
- [ ] Killing the process mid-sequence and restarting resumes at the first unfinished step
- [ ] Steps already completed are not run again on resume
- [ ] A step that needs human input ends the sequence and parks the task, rather than blocking on an answer
- [ ] A parked task resumes at the correct step once the human answers, including across a restart
- [ ] A later step can read only the declared structured result of earlier steps, not their internal activity
- [ ] The number of tasks running at once respects the configured limit
