# 12: Memory kinds register themselves

**What to build:** A memory kind ships with its plugin via `MemoryKindSpec`, instead of being hard-coded in the reader/writer maps.

**Blocked by:** 10.

**Status:** ready-for-agent

- [ ] `_READERS`/`_WRITERS` and the `*Data` shapes come from a `MemoryKindSpec` registry
- [ ] `MemoryKindSpec` is trimmed to the fields the registry uses (`name`, `data`, `writers`, `cardinality`, `injected`); `schema_version`/`upgrade`/`sensitivity`/`allowed_scopes`/`audience`/provider-policy are omitted until their trigger, each added with a test
- [ ] `MemoryKind` is a validated string; `ModelMemoryKind` stays closed
- [ ] `test_memory_kinds.py` asserts against the registry
- [ ] `uv run pytest -q` passes
