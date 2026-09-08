# 09: Performance budget gate (Lighthouse CI)

**What to build:**

A regression gate that runs Playwright + Lighthouse against `web/dist`
on every commit. The audit asked for "13 problems caught by
regression tests" and the two performance findings (SSE burst
re-renders, skeleton/content shape mismatch) are caught by
Lighthouse's own metrics — CLS in particular.

Budgets, all fail-the-build:

- LCP ≤ 1.0s on `/monitor`
- CLS ≤ 0.05
- TBT ≤ 200ms
- Total JS bundle ≤ 200KB gzipped
- Single JS file ≤ 100KB gzipped (catches one big file hiding
  the size budget across many small ones)
- Initial render of `/monitor` ≤ 500ms after navigation

A separate bundle test reads `web/dist/assets/*.js` after `npm run
build` and asserts both limits. Bundle size is the one budget that
is fast to check and catches the most regressions, so it runs
unconditionally; Lighthouse only runs if the bundle passes.

**Blocked by:** 03, 06, 07.

**Decisions:** D5, audit notes #4, #5.

**Status:** ready-for-agent

- [ ] `tests/test_bundle.py` reads `web/dist/assets/*.js` after
      `npm run build` and fails if any single file exceeds 100KB
      gzipped or the sum exceeds 200KB gzipped. Runs as part of
      `uv run pytest -q` without external services.
- [ ] `tests/test_perf.py` runs Playwright + Lighthouse against
      `web/dist` served on a random port. The bundle test runs
      first; if it fails, the perf test is skipped — the operator
      already knows what to fix.
- [ ] Budgets: LCP ≤ 1.0s, CLS ≤ 0.05, TBT ≤ 200ms, initial
      render ≤ 500ms. Each is a separate assertion; the report
      on failure names which budget was exceeded and by how
      much, so a regression is one fix away.
- [ ] `tests/test_perf.py` runs only when `--run-perf` is
      passed. Lighthouse needs a real browser and adds a few
      seconds to the suite — most PRs only need the bundle test
      to catch regressions. CI enables `--run-perf`; local
      development runs without it.
