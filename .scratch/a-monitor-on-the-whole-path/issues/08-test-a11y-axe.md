# 08: a11y audit gate (axe-core)

**What to build:**

A regression gate that runs axe-core against every screen at commit
time. It runs as part of `uv run pytest -q`, so a serious or
critical violation fails the build before it ships — the audit
called for "13 problems the design-system audit found, regression
tests on every fix" and the test that catches the regression is
this one.

The five "Critical" audit findings from `notes.md` all surface here:

- missing `aria-live` on the Monitor feed (audit #1 — applied in
  ticket 04, this test is what keeps it applied)
- color-only state indicators (audit #2)
- contrast violations against the dark palette
- keyboard traps introduced by modals (e.g. the Dialog)
- missing labels on icon-only buttons

axe-core covers all five; the test does not invent project rules.

**Blocked by:** 03, 06, 07.

**Decisions:** D5, audit notes #1, #2, #3, #6.

**Status:** ready-for-agent

- [ ] `tests/test_a11y.py` builds `web/dist` once (via
      `npm run build`), serves it through the same FastAPI app
      used in `tests/test_web_contract.py`, and runs
      `@axe-core/playwright` against `/`, `/board`, `/rooms`,
      `/flow/discord/<seed>`.
- [ ] The test fails on any violation with severity `serious`
      or `critical`. `moderate` and `minor` are reported but do
      not fail the build — the audit's rule is "block the
      regression", not "ship perfect".
- [ ] Each page renders cleanly under axe-core; a regression
      that drops `aria-live` from the Monitor feed, hides the
      focus ring, or removes a button label fails the build.
- [ ] `web/src/keyboard.ts` exposes a focus trap for the
      shortcut overlay (audit #6) so tabbing inside it does not
      escape. The Dialog already wraps focus; the overlay's
      trap does the same.
- [ ] `tests/test_a11y.py` runs in CI without external
      services. axe-core and playwright are dev dependencies
      added to `pyproject.toml`.
