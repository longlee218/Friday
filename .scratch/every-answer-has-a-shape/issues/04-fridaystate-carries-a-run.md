# 04: FridayState carries a run, and memory reads its scope from it

**What to build:** one state object that a message's journey carries — which
provider, channel, thread and message, who wrote it, what it replies to, the task
it became, and which agent is running (D8). Its fields are read-only and every
change goes through a named method that returns a new state (D9), so "what can
change this, and where" is a list somebody can read rather than an assignment
anywhere.

This ticket **expands**: the new state exists beside the old scope and the memory
tools read from it. Nothing about who may read a memory changes — scope is still
runtime-supplied and never named by the model, and a channel's memory is still
invisible to a run in another one (D15). Deleting the old name is ticket 06.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] A field cannot be assigned from outside; a test says so rather than the docstring.
- [ ] Each named method returns a new state with exactly one thing changed and leaves the original untouched.
- [ ] The memory tools read channel, task, agent and message from the state, and a memory lands under the room the run was actually about.
- [ ] The store's memory methods accept it.
- [ ] A run in one channel still cannot see another channel's memory.
