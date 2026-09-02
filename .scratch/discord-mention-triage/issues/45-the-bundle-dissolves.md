# 45: The bundle dissolves; the shared module is mechanism only

**What to build:** Delete `ContextBundle`. After 42 and 43 nothing builds one —
triage renders its one section, the responder renders its own ordered list —
so the ten-slot dataclass with a fixed order for every family is the last
piece of shared *shape*, and shape was ruled per-family when the families were
split. The shared prompt module shrinks to what is genuinely mechanism: the
section type, the escaping, the value renderers.

**Blocked by:** 42, 43 (the two families that still construct a bundle)

**Status:** done

## Why a separate ticket

This is the contract step of an expand–contract: 42 and 43 make the bundle
unused without breaking anything; this one deletes it and proves nothing
reaches for it. Folding the deletion into whichever of the two lands last
makes that ticket's green depend on landing order.

## Acceptance criteria

- [x] `ContextBundle` is gone, and so is every builder only it used
- [x] The shared module holds mechanism only — nothing in it names a family,
      a section order, or a piece of wording
- [x] The one-seam escaping test still passes, and the hygiene tests
      (docs paths, `__all__`, prompt orphans) still pass
- [x] A grep test pins the new line: no family imports another family's
      prompt module

## What it came to

`ContextBundle`, the `Builder` alias and the `notes()` section builder are
gone — the last had no caller but tests, since promoted notes reach a prompt
through the harness. The shared module now holds: `Section`, nine section
builders, three value renderers, one escape. Nothing in it names a family or
an order. New grep guard: no family imports another family's prompt module.
Byte-identical, ten of ten, across all four tickets.
