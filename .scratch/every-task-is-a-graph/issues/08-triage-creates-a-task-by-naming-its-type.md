# 08: Triage creates a task by naming its type

**What to build:** One tool creates a task and takes the type as an argument;
`skip` stays its own tool because it creates nothing. Adding a task type
becomes adding one params class — no new tool, no second place to describe
what the type means.

**Blocked by:** None (can start immediately)

**Decisions:** D16, D17, D18

**Status:** done

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

- [x] Triage has two tools: `create_task(task_type, confidence)` with a closed
      enum of types, and `skip(confidence)`
- [x] Each type's description in the enum is generated from the same place the
      type's fields are defined; adding a type is adding one params class
- [x] Triage still asks for nothing but a confidence — the existing test that
      pins that keeps passing, against the new schema
- [x] Forced tool choice and stop-on-first-tool are unchanged, so the vaguest
      message still produces a call rather than silence
- [x] The confidence threshold and the follow-up rules still decide whether a
      task exists
- [x] No graph node is given a task-creating tool (D18), pinned by a test
- [x] Exempt from the byte-identity rule — D16 changes the triage tool schema,
      which is part of the prompt

## What it came to

`create_api_issue_task`, `create_access_request_task` and
`create_doc_question_task` collapse into one `create_task(task_type,
confidence)`. `task_type`'s enum is `Literal[tuple(PARAMS)]` — built from the
same registry `friday.workflows.PARAMS` already was — so its members can never
drift from the types that actually have a `Params` class.

The per-value description in the tool's schema (what the model reads to
choose between them) is generated from each `Params` class's own one-line
docstring: `ApiIssueParams.__doc__`, `AccessRequestParams.__doc__`,
`DocQuestionParams.__doc__`. They used to live in the tool docstrings, three
model-facing sentences duplicating what the tool selection already encoded.
Griffe (the docstring parser the SDK's schema generator uses) only kept the
first line of each `Params` docstring it read — worth knowing before writing
one with an explanation attached, since the explanation would have silently
become part of what the model reads too. `skip` is untouched: it creates
nothing, so folding it into `create_task` would make the tool's name a lie.

Two new tests: one asserts the enum and its description are read from
`PARAMS`, not hand-maintained (mutation-tested — dropping a type from the
enum turns it red); one is a hygiene test pinning D18 — no module under
`friday/dag/` may import `friday.triage`, which is the only way a node could
reach `create_task` (also mutation-tested). The existing "asks for nothing but
confidence" test now allows `task_type` too, with its docstring explaining
why: it is not extracted content, it is what used to be *which tool got
called*.

612 tests, up from 610 (two new). `friday/triage/prompt.py` untouched — only
the tool schema changed, which is why this ticket is exempt from byte-identity
rather than passing it trivially.
