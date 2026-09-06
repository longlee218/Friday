# 03: The page writes back, and only to `overrides`

**What to build:** Routes to read a channel's context, edit its `overrides`,
create the file for a channel that has none, and reload every context file into
the running process.

**Blocked by:** None

**Decisions:** D7, D8, D9

**Status:** todo

## Why

This is the ticket that reverses a recorded constraint, so it is the ticket
that has to argue for it.

`docs/SPEC.md`, Out of Scope: *"Any interaction on the web page. It displays
state; decisions happen in Discord."* Story 42: *"read-only, so that there is
exactly one place where decisions are made."* `docs/DESIGN.md`: *"a debug view,
not a control panel."* Ticket 18 of `discord-mention-triage`: *"The page still
offers no way to change anything."*

**The reversal turns on a distinction the original rule never had to make.**
That rule exists so **decisions** have one home. A channel's `overrides` is not
a decision — it is context, what is true about a room, and the merge order
`base < derived < overrides` puts it in the one section the machine is
forbidden to touch (`rebuild_derived` never writes it; `init_channel` refuses
to overwrite an existing file). It was created for the operator, and in the
whole life of this system **nothing has ever written it except a CLI script**.
`context/` is empty. The mechanism designed for exactly this has never been
used once.

Nothing that decides anything becomes writable here: not a task's state, not an
approval, not a classification, not agent memory. Those stay in Discord.

**One rule for when an edit takes effect (D8).** `ContextStore._held`'s own
comment refuses the alternative: *"Read once at startup… and two rules for
'when does my edit take effect' is one too many."* Writing from the page and
taking effect at once, while a hand-edit of the same file waits for a restart,
is precisely those two rules. So there is an explicit reload, and it calls
`hold_all()` — re-reading *every* file, so a hand-edit and a page edit are
picked up by the same action. This leaves hand-editing better off than it is
today, where it needs a process restart.

**Key/value pairs, not YAML (D9).** `merged()` is `{**base, **derived,
**overrides}` — a flat dict with no schema, and `derived` holds exactly one key
today. A raw-YAML textarea makes "this channel's file is malformed and it now
has no context" a state the UI can produce. Pairs make it unreachable.

## Acceptance criteria

- [ ] `GET` a channel's context returns the three layers separately — `base`,
      `derived`, `overrides` — and the merged result, so the page can show what
      the operator's edit is actually overriding
- [ ] `PUT` replaces a channel's `overrides` wholesale from key/value pairs;
      `derived` and `state` come back from disk untouched, byte for byte
- [ ] Creating a channel's file goes through `ContextStore.init_channel`, whose
      `FileExistsError` guard is kept and surfaces as a real HTTP status rather
      than a 500
- [ ] Reload re-reads every file, not just the one just written, and the next
      prompt assembled uses the new value — proven by a test, not by inspection
- [ ] Values are stored **plain**. `ChannelContext`'s docstring is the rule:
      *"Every value here is plain text. Escaping happens once, on the way into
      a prompt."* A value escaped on the way in reaches the model as
      `&amp;lt;b&amp;gt;`
- [ ] A key that would collide with `derived`'s own keys is allowed — that is
      what an override *is* — but the page is told, since silently shadowing
      the summariser's work is a surprise worth one line of UI
- [ ] Listing channels works when `context/` is empty, which is its state today
- [ ] Tests cover: the write reaching a prompt after reload, `derived`
      surviving a write to `overrides`, the create-guard, plain storage, and an
      empty `context/`

## Notes

The API must hold the same `ContextStore` instance the agents read from —
`run_agent.py:109` builds one and hands it to the responder, `register_dags`
and the rebuilder. One process (the architecture's first constraint) is what
makes this a reference rather than an IPC problem. `build_api()` gains a
parameter.

`ContextStore._write` is *"the only `write_text` in `friday/`"*. Keep it that
way — go through the store, do not open the file from a route.

`friday/ops/api.py`'s docstring currently frames the module as egress only:
*"This is the last place anything leaves the process."* Adding ingress changes
what that module is, and the docstring must say so rather than becoming quietly
false.
