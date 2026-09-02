# 08: Triage creates a task by naming its type

**What to build:** One tool creates a task and takes the type as an argument;
`skip` stays its own tool because it creates nothing. Adding a task type
becomes adding one params class — no new tool, no second place to describe
what the type means.

**Blocked by:** None (can start immediately)

**Decisions:** D16, D17, D18

**Status:** ready-for-agent

## Why

There is one tool per task type today, and each one's docstring describes the
type in prose written a second time — the first time being the params class
that says what fields the type has. Adding a fourth type means writing a tool,
a docstring, an entry in the tool list, an entry in the params registry and an
entry in the extraction registry, and a test already exists because two of
those five have drifted apart before.

`create_task(task_type, confidence)` with a closed enum collapses the first
three. The per-type descriptions are generated from the params registry, so
the type is described where its fields are defined.

`skip` stays separate. A tool named `create_task` that creates nothing is
lying in its name, and the model reads that name.

Nothing about who decides changes: the model proposes a type and a confidence,
and the threshold plus the follow-up rules still decide whether a task exists.

## Acceptance criteria

- [ ] Triage has two tools: `create_task(task_type, confidence)` with a closed
      enum of types, and `skip(confidence)`
- [ ] Each type's description in the enum is generated from the same place the
      type's fields are defined; adding a type is adding one params class
- [ ] Triage still asks for nothing but a confidence — the existing test that
      pins that keeps passing, against the new schema
- [ ] Forced tool choice and stop-on-first-tool are unchanged, so the vaguest
      message still produces a call rather than silence
- [ ] The confidence threshold and the follow-up rules still decide whether a
      task exists
- [ ] No graph node is given a task-creating tool (D18), pinned by a test
- [ ] Exempt from the byte-identity rule — D16 changes the triage tool schema,
      which is part of the prompt
