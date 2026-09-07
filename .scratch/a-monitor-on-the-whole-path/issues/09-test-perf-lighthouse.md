# 09: Performance budget gate (Lighthouse CI)

**What to build:**

`tests/test_perf.py` runs Playwright + Lighthouse against `web/dist`
served on a random port. Budgets, all fail-the-build:

- LCP ≤ 1.0s on `/monitor`
- CLS ≤ 0.05
- TBT ≤ 200ms
- Total JS bundle ≤ 200KB (gzipped)
- Initial render of `/monitor` ≤ 500ms after navigation

The bundle budget is enforced by a separate `tests/test_bundle.py`
that reads `web/dist/assets/*.js` after `npm run build` and fails if
any single file exceeds 100KB or the sum exceeds 200KB.

The two "Performance" audit findings from `notes.md` (SSE burst
re-renders, skeleton/content shape mismatch) are caught here too:
Lighthouse measures Cumulative Layout Shift directly.

**Blocked by:** 05, 06.

**Status:** ready-for-agent
