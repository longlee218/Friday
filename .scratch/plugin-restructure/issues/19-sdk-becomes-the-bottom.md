# 19: sdk becomes the bottom — fold `domain` away

**What to build:** The dependency stack matches DESIGN-v2's end state (§13). `friday/sdk` is the true bottom of our code — Protocols plus the pure, dependency-free values and helpers everything shares — and there is no separate `friday/domain` layer above it any more: the shared pure pieces move *into* `sdk`, and the rest of `domain` (the models) moves *under* `kernel`. A plugin still imports `sdk` only. Pure structural move — no behaviour change.

**Blocked by:** 15.

**Priority:** highest — this is the structural-cleanup track (DESIGN-v2 alignment); it runs before 16 and 17. First of three slices (19 → 20 → 21). It is a wide refactor (the `friday.domain` → new-home rename touches every importer); land it as one coordinated rename so the suite is green before and after, not a half-migrated tree.

**Status:** ready-for-agent

- [ ] The workflow actions (`Action`/`Ask`/`Reply`/`HandOver`) and the pure shared helpers a plugin codes against — the params validation DSL (`InSet`/`Matches`/`OneOf`/`validate`/`Problem`/`asked_as`), the prompt-assembly primitives (`Section`/`assemble`/`role`/`job`/… + the escapers), and `scrub` — **live in `friday/sdk` itself**, no longer re-exported from `friday.domain`. `sdk` imports nothing of ours.
- [ ] The rest of `friday/domain` — the models (`Task`, `Memory`, the `*Data` shapes, `FridayState`, `MentionType`, …), `states`, `conversation`, `memory_guard`, and the triage value types (`Decided`/`make_decided`/`SKIP`) — moves under `friday/kernel/domain/`. Nothing imports `friday.domain` afterwards.
- [ ] `tests/test_dependency_rule.py` asserts the end-state layering and nothing weaker: `sdk` imports nothing of ours; `kernel` imports `sdk` (and itself, plus third-party); a plugin imports `sdk` only; the kernel names no plugin (no plugin import, no task-type/pack-kind literal). Each guard deleted once and watched go red (Rule 13).
- [ ] Clean code: no re-export shims left behind for the moved symbols, no `friday.domain` references in comments/docstrings, every touched module reconciled to the new layering.
- [ ] `uv run pytest -q` passes; `test_a_plugin_imports_sdk_only` stays non-vacuous.
- [ ] `docs/DESIGN.md` § What exists and `CONTEXT.md` updated to the as-built layering (sdk at the bottom, domain folded); no behaviour change.
