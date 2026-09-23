# 12: Memory kinds register themselves

**What to build:** A memory kind ships with its plugin via `MemoryKindSpec`, instead of being hard-coded in the reader/writer maps.

**Blocked by:** 10.

**Source:** `spec.md` — Migration order, step 5 (memory kinds register, trimmed spec); § Implementation Decisions → "Registration and scope".

**Status:** done

- [x] `_READERS`/`_WRITERS` and the `*Data` shapes come from a `MemoryKindSpec` registry
- [x] `MemoryKindSpec` is trimmed to the fields the registry uses (`name`, `data`, `writers`, `cardinality`, `injected`); `schema_version`/`upgrade`/`sensitivity`/`allowed_scopes`/`audience`/provider-policy are omitted until their trigger, each added with a test
- [x] `MemoryKind` is a validated string; `ModelMemoryKind` stays closed
- [x] `test_memory_kinds.py` asserts against the registry
- [x] Clean code: remove the dead code, outdated comments and now-unused imports/functions this change leaves behind, and reconcile the modules it touched against the new `sdk`/`kernel`/`plugins` structure — nothing left in the old shape
- [x] `uv run pytest -q` passes
