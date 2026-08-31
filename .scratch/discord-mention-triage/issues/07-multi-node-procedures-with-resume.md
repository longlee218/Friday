# 07: Deterministic workflow per task type

**What to build:** A task runs an ordered sequence of reasoning steps, and interrupting
the service mid-sequence resumes where it left off instead of starting over. Proven
against a stand-in sequence, since the real ones are not yet specified.

**Blocked by:** 04, 05

**Status:** superseded

- [ ] A stand-in two-step sequence runs to completion and records each step's result before the next begins
- [ ] Killing the process mid-sequence and restarting resumes at the first unfinished step
- [ ] Steps already completed are not run again on resume
- [ ] A step that needs human input ends the sequence and parks the task, rather than blocking on an answer
- [ ] A parked task resumes at the correct step once the human answers, including across a restart
- [ ] A later step can read only the declared structured result of earlier steps, not their internal activity
- [ ] The number of tasks running at once respects the configured limit

## Superseded

The original shape of this ticket — a runner executing sequences of agentic nodes,
with per-node checkpointing and resume — assumed every step was an LLM call.

That decision was reversed: workflows are **deterministic Python** for the first
version, and only promoted to agentic per type once the deterministic one has
proven itself. Ordinary branching needs no checkpointing, no resume, and no node
runner.

What remains here is much smaller: one workflow per type, deciding what action a
task needs from its parameters.

    api_issue:
        missing correlation_id and curl  -> ask for them
        otherwise                        -> trace, then answer

Rewrite the criteria above before starting; they describe machinery that is no
longer being built.


## Superseded, and by what

This described a task running an ordered sequence of reasoning steps, checkpointed
so that killing the process mid-sequence resumed at the first unfinished one. It
was written for the design where a procedure was a list of nodes and a suspended
coroutine could not be persisted.

That is not what shipped. A workflow is a **planner**: one function of the task's
parameters returning one action. It reads, it decides, and it writes nothing until
it returns — so a planner interrupted halfway has produced no state to lose, and
the next poll simply runs it again. There is no position to resume from because
there is no position.

The multi-step case it was really for is still reachable and needs no machinery: a
planner that has to do two things does them in one coroutine, and if the first is
expensive enough to be worth not repeating, its result belongs in the task's
parameters where the next pass will find it.

Reopen this if a step ever becomes expensive enough that re-running it costs more
than checkpointing it would. Nothing is near that today.
