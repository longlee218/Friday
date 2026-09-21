# 19: A row that names another row should not be typed

**What to build:** A referential check where structured rows are written, a
`service.project` the operator picks rather than types, and a `repo_path`
chosen from the filesystem rather than spelled.

**Blocked by:** nothing. 09 built the form this extends.
**Decisions:** the operator's, 2026-09-21.
**Status:** ready-for-agent

## Why

The knowledge table has a foreign key and nothing knows it is one. The join
is two lines:

```python
# friday/domain/models.py:737
MemoryKind.PROJECT: lambda: d.get("name"),     # a project row's key is data.name

# friday/dag/api_issue/resolve.py:205
structured_memory(channel_id, kind=MemoryKind.PROJECT, key=service.project)
```

String equality between two free-text fields typed by hand on different
days, in different forms, with nothing checking that they agree.

**It failed on the first real use.** The six rows ticket 00 needs were
entered on 2026-09-21. Five were right. The sixth named the project
`backend-reelme-v2` while the service row pointed at `reelme-v2`, so
`ReadFailingCode` skipped with "no project row names a repository" — a
message that does not say *which* name it looked for, on a row that looks
correct in the form, in a system with no other symptom.

Nothing was wrong with the operator's care. The form asked for a foreign key
as free text, which is a question whose wrong answers look exactly like its
right ones.

## What

**Step 0, and it is one line.** `read_failing_code`'s skip names the project
it looked for. Every step below is worth doing and none of them makes the
existing failure legible after the fact; this does.

**A referential check where rows are written.** `Database.memory_add` (and
`memory_update`) refuse a `service` whose `project` names no active
`project` row in this channel or in `'*'`, the same scope
`structured_memory` reads. At the store rather than in the form, because the
form is one door and the API is another — and it is `memory_add`'s own
argument that the operator's hand meets the same check a model's does.

Open questions this raises, to answer in the ticket rather than discover:
- Deleting a `project` a `service` still names. Refuse, or allow and let the
  reader hand over? A soft-delete makes this recoverable either way.
- `'*'` rows pointing at a room's rows, which would resolve for one room and
  not another.

**And a third, found the same day by the next edit.** A structured row's key
*is* its data — `project`'s key is `data.name` — so **renaming a row
silently orphans every row that names it**. Within twenty minutes of the
first mismatch being fixed, `service.name` was edited from
`backend-reelme-v2` to `reelme-v2`, and both `route` rows went on pointing
at a service that no longer existed. The chain broke one link earlier than
before, in a row nobody had touched.

A dropdown does not help here: the operator was editing the row being named,
not the row doing the naming. So the check has to run on the **rename** as
well as on the write — `memory_update` refusing to move a key that another
active row still points at, naming the rows that do.

**`service.project` as a choice.** `friday/ops/api.py`'s `_form_fields`
already emits `{"type": "choice", "choices": [...]}` — it does it for any
`Literal`, which is how `env` became a dropdown. What is missing is that
`/api/memory-kinds` knows no channel, so it cannot offer *this room's*
project rows. It becomes channel-aware, or a sibling route does, and the
field declares that its choices are rows rather than literals. The frontend
needs no change: `RoomsScreen` renders whatever fields that endpoint
describes (`web/src/api-types.ts`, `MemoryField`).

**`repo_path` chosen, not typed.** The board is loopback-only on the
operator's own machine, so a route that lists directories is possible where
it would not be on a server. It must be confined to a configured root, with
the same guard `friday/sources/code.py:repo_file` already applies to a stack
frame — resolve, then refuse anything that lands outside the root. Never an
unbounded filesystem browser, and never a path from the client trusted as
given.

## Verify

- A test that fails today: write a `service` naming a project that does not
  exist; assert it is refused with the name it could not find.
- The refusal reaches the form as a 422 carrying the store's own reason, the
  way every other refusal does.
- `/api/memory-kinds` for a channel with two project rows offers exactly
  those two as `service.project`'s choices, and a channel with none offers
  none — which is itself the signal that the project row comes first.
- The directory route refuses `..` out of its root, and a symlink pointing
  out of it, and says nothing about what is there.
- Each guard deleted once and watched go red.

## Not this ticket

The other free-text keys — `route.service` naming a `service`,
`dependency.from_service`/`to_service`, `service.project` — are the same
shape and should follow the same mechanism once it exists. They are listed
here so the first implementation is built as a mechanism rather than as one
special case, not so that it ships all of them at once.
