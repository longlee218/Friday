# 08: a11y audit gate (axe-core)

**What to build:**

`tests/test_a11y.py` builds `web/dist`, serves it through the same
FastAPI app, and runs `@axe-core/playwright` against the Monitor, Board,
Rooms, and Flow pages. The test fails on any `serious` or `critical`
violation. It runs as part of `uv run pytest -q`, so a regression is
caught at commit time, not in production.

The five "Critical" audit findings from `notes.md` all surface here:

- missing `aria-live` on the feed (audit #1)
- color-only state indicators (audit #2)
- contrast violations
- keyboard traps introduced by modals
- missing labels on icon-only buttons

The test uses `axe-core`'s ruleset; no project-specific rules. What
axe cannot catch is left to a separate manual review checklist in
`notes.md`.

**Blocked by:** 06, 07.

**Status:** ready-for-agent
