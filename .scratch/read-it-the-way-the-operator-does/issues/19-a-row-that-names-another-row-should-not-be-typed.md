# 19: A row that names another row should not be typed

**What to build:** A referential check where structured rows are written, a
`service.project` the operator picks rather than types, and a `repo_path`
chosen from the filesystem rather than spelled.

**Blocked by:** nothing. 09 built the form this extends.
**Decisions:** the operator's, 2026-09-21.
**Status:** done 2026-09-21

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


## Done — the check, 2026-09-21

**Step 0 first, because it is what makes the rows already written legible.**
`read_failing_code`'s skip now names what it looked for: "the service
`backend-reelme-v2` names a project no row here is called". The old message
said only that no project row named a repository, which is the symptom the
first six rows produced and the reason the mismatch took twenty minutes to
find.

**The declaration is derived, not listed.** `names_in(kind)` reads
`field(metadata={"names": ...})` off the kind's schema and `named_by(kind)`
reverses it, so a new foreign key is declared in one place and checked
everywhere by that alone.

**Three checks at the store, which is the door both the form and the API go
through:**

- `memory_add` and `memory_update` refuse a row naming a row that does not
  exist, in the scope `structured_memory` reads by — this room or `'*'`.
- `memory_update` refuses **moving a key** another active row still names.
  That was the second failure, twenty minutes after the first: a dropdown
  cannot help when the operator is editing the row *being* named.
- `memory_delete` refuses removing a row another still names, naming them.
  The ticket left this open — refuse, or allow and hand over later. Refused:
  the breakage would otherwise surface hours afterwards inside a graph run,
  which is the failure this exists to stop, and a soft delete makes the
  work-around cheap (remove the dependant first).

**The `'*'` question is answered too.** A `'*'` row may only name another
`'*'` row: "true everywhere" cannot depend on something that exists in one
room, or it resolves for that room and nowhere else.

**What it cost, and it is worth saying.** Twenty-four existing tests went
red — every fixture that wrote a `service` or a `route` in isolation. That is
what a foreign key does, and the fix was ordering, not weakening: a helper
derived from `names_in` seeds the chain under whatever a test writes.

**One branch is now unreachable by typing and still reachable.**
`read_failing_code`'s "no project row" skip cannot be produced through the
store any more — but the live database holds rows written before this check,
and that node is what reads them. Its test builds the envelope directly and
says so.

### What the review found, and it was two more doors

- **`memory_supersede` took `data` too**, so it could move a key or name a
  row that does not exist — the same two failures, through a third door. The
  docstring said "the form is one door and the API is another"; there were
  three. (Found by probing before the review arrived, and confirmed by it.)
- **A `'*'` row's dependants were searched in `'*'` alone.** `_dependants`
  scoped to `[channel_id, "*"]`, which with `channel_id == "*"` collapses to
  one scope — so a shared project could be removed while one room's service
  still named it. A `'*'` row is named from everywhere, so its dependants are
  now searched everywhere.
- **Four tests passed with the check removed.** They were negative controls
  and legitimate as such, except one that claimed to pin the `'*'` scope and
  pinned nothing — which is exactly where the bug above lived. Both
  directions of that scope now have a test that goes red without the code.

The check-then-write is not atomic with the insert. One process holds one
`Database` and the operator is one person, so the window is theoretical; it
is written down in the docstring rather than defended against, because
defending it would mean a lock around every structured write for a race
nobody has met.

### And the picker, the same day

`repo_path` is chosen now. `GET /api/directories` lists the directories
under `config.yaml`'s `repo_root` — **directories only**, because the
operator is choosing a repository and a file listing is a view of their
machine nothing here needs; dotted ones are left out, because `.git` is
never the answer to "which repository" and offering it is offering a wrong
choice that looks like one.

**The confinement is what makes the route defensible**, and it is the guard
`sources/code.py:repo_file` already applies to a stack frame: resolve
first, then refuse anything landing outside the root. Resolving *first* is
what catches a symlink whose name is inside the root and whose target is
not. A path from a client is a path from outside, whatever it looks like.

**Off unless an operator says where.** No `repo_root`, no route — a board
that browses `/` by default is one nobody meant to switch on, and the field
simply goes back to being typed.

Declared on the field (`picks: "directory"`) the way `names` is, so the page
has no list of its own to disagree with, and the picked path stays editable
as text: correcting a row that already holds a path should not mean walking
to it again.

One thing worth remembering from writing it: `friday/ops/api.py` imports
`Path` from **FastAPI**, where it declares a path *parameter*. Using it as
`pathlib.Path` is a `TypeError` three lines later, and five tests found it
at once.
