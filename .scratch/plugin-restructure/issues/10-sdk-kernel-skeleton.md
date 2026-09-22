# 10: sdk contracts + kernel skeleton + dependency-rule test

**What to build:** The `sdk`/`kernel` boundary exists and is enforced — a set of contracts a plugin registers against, and a kernel that names no plugin — so later tickets can register capabilities instead of editing the core.

**Blocked by:** 06.

**Source:** `spec.md` — Migration order, step 7 (`sdk`/`kernel`/`plugins` split); § Implementation Decisions → "Shape and trust".

**Status:** ready-for-agent

- [ ] `friday/sdk` holds `Plugin`, `PluginAPI`, `TaskTypeSpec`, `MemoryKindSpec` (beside the workflow port from 06): Protocols and dataclasses only, no I/O, no third-party imports
- [ ] `friday/kernel` imports `sdk`; a plugin imports `sdk` only
- [ ] Dependency-rule `ast` test (seam S4): `sdk` imports nothing of ours, `kernel` imports `sdk`, a plugin imports `sdk` only
- [ ] "Kernel names no plugin": no plugin import and no task-type/pack-kind literal in `friday/kernel` (guard deleted once and watched go red)
- [ ] `uv run pytest -q` passes
