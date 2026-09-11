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

**Status:** done

- [x] A field cannot be assigned from outside; a test says so rather than the docstring.
- [x] Each named method returns a new state with exactly one thing changed and leaves the original untouched.
- [x] The memory tools read channel, task, agent and message from the state, and a memory lands under the room the run was actually about.
- [x] The store's memory methods accept it.
- [x] A run in one channel still cannot see another channel's memory.

## Comments

**Done together with ticket 06, and that is a judgement about this refactor
rather than a shortcut.** The board planned expand–contract because replacing
`MemoryScope` looked wide. Measured, it is eight production sites, and every
one reads the same four fields under the same names — the store’s memory
methods needed an annotation changed and nothing else. Expand–contract earns
its cost when a single edit breaks thousands of call sites and no slice can
land green; here it would have bought one commit of adapter written to be
deleted in the next.

`FridayState` lives in `friday/domain/models.py`, where `MemoryScope` was, for
the reason recorded there: it is part of the store’s own signature, and a store
may not import from `friday/tools/`.

Only `channel_id` and `agent` are required — the boundary and the provenance.
The rest are `None` until the journey supplies them, which is what `task_id`
already meant. There is deliberately no general `with_(**fields)`: a generic
setter makes every change legal again and puts "what can change this, and
where" back out of reach.

What it is for is visible at `Pool._say`: the responder took `channel_id`,
`task_id` and `message_id` as three parameters, and the pool built all three
from a conversation it was already holding. One object now.

**No `for_event` yet**, though the shape has room for one. Triage is where a
message’s journey actually starts, and triage’s context is a capture until
ticket 07; a constructor with no caller is the speculative generality this
board is otherwise removing.
