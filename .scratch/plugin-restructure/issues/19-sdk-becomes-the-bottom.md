# 19: sdk becomes the bottom — fold `domain` away

**What to build:** The dependency stack matches DESIGN-v2's end state (§13). `friday/sdk` is the true bottom of our code — Protocols plus the pure, dependency-free values and helpers everything shares — and there is no separate `friday/domain` layer above it any more: the shared pure pieces move *into* `sdk`, and the rest of `domain` (the models) moves *under* `kernel`. A plugin still imports `sdk` only. Pure structural move — no behaviour change.

**Blocked by:** 15.

**Priority:** highest — this is the structural-cleanup track (DESIGN-v2 alignment); it runs before 16 and 17. First of three slices (19 → 20 → 21). It is a wide refactor (the `friday.domain` → new-home rename touches every importer); land it as one coordinated rename so the suite is green before and after, not a half-migrated tree.

**Status:** done

- [x] The workflow actions (`Action`/`Ask`/`Reply`/`HandOver`) and the pure shared helpers a plugin codes against — the params validation DSL (`InSet`/`Matches`/`OneOf`/`validate`/`Problem`/`asked_as`), the prompt-assembly primitives (`Section`/`assemble`/`role`/`job`/… + the escapers), and `scrub` — **live in `friday/sdk` itself**, no longer re-exported from `friday.domain`. `sdk` imports nothing of ours. (`sdk/actions.py`, `sdk/validation.py`, `sdk/prompt.py`, `sdk/redact.py`; `test_sdk_imports_nothing_of_ours` green.)
- [x] The rest of `friday/domain` — the models (`Task`, `Memory`, the `*Data` shapes, `FridayState`, `MentionType`, …), `states`, `conversation`, `memory_guard`, and the triage value types (`Decided`/`make_decided`/`SKIP`) — moves under `friday/kernel/domain/`. Nothing imports `friday.domain` afterwards. (`SKIP` at `kernel/domain/models.py`; `make_decided`/`Decided` at `kernel/domain/triage.py`; `grep friday.domain` returns only updated historical prose.)
- [x] `tests/test_dependency_rule.py` asserts the end-state layering and nothing weaker: `sdk` imports nothing of ours; `kernel` imports `sdk` (and itself, plus third-party); a plugin imports `sdk` only; the kernel names no plugin (no plugin import, no task-type/pack-kind literal). Each guard deleted once and watched go red (Rule 13). (All 5 guards verified red when violated, green with probes removed.)
- [x] Clean code: no re-export shims left behind for the moved symbols, no `friday.domain` references in comments/docstrings, every touched module reconciled to the new layering. (Two pre-existing dead refs to `friday/domain/tasks.py` — a path that never existed — left untouched per surgical-changes.)
- [x] `uv run pytest -q` passes; `test_a_plugin_imports_sdk_only` stays non-vacuous. (1568 passed, 1 skipped.)
- [x] `docs/DESIGN.md` § What exists and `CONTEXT.md` updated to the as-built layering (sdk at the bottom, domain folded); no behaviour change.

**Note (Rule 4 — triage eval):** `make_decided` is upstream of the triage classifier, but this is a **verbatim** relocation (byte-identical body/docstring), so no classifier behaviour changed. The eval (`python -m evals.run_triage_eval`) is currently **provider-quota-blocked** (HTTP 429, MiniMax token plan exhausted) — accuracy unmeasurable. The available signal, **0/35 decisions outside the closed set**, confirms the label set is intact. Re-running for an accuracy/confusion figure is an operator action (restore provider quota).
