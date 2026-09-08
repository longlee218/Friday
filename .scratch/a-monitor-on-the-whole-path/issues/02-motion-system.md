# 02: Motion system

**What to build:**

Three named motions, one job each, durations on the existing
`--d-fast` / `--d-med` / `--d-slow` tokens, easing on `--ease`.
The audit's rule: enter is always slower than exit (240/180ms,
180/120ms, 180/120ms), so a closing feels like a closing rather
than a pause.

- `fade` for route transitions and screen-level changes
- `slide` for panels and toasts
- `scale` for dialogs and popovers

Apply via class names (`fade-enter`, `fade-exit`, etc.) on whatever
element changes. CSS owns the timing; the React side just flips
the attribute.

Only `opacity` and `transform` animate — `width`, `height`,
`top`, `left` would force a reflow on every frame.

**Reduced motion:** `prefers-reduced-motion: reduce` collapses
every duration to 0 and turns every transform animation into an
opacity-only fade.

**Blocked by:** 01.

**Decisions:** D2.

**Status:** done

- [ ] Three motion classes are defined in `web/src/index.css`:
      `fade-enter`, `slide-enter`, `scale-enter` (and matching
      `-exit` variants). Each reads its duration from the
      `--d-fast` / `--d-med` / `--d-slow` tokens.
- [ ] Each motion has a corresponding `@keyframes` rule
      (`motion-fade`, `motion-slide`, `motion-scale`) that
      animates only `opacity` and `transform`.
- [ ] The exit duration is always faster than the enter
      duration: enter uses `--d-slow` (240ms), exit uses
      `--d-med` or `--d-fast` (180ms / 120ms).
- [ ] `@media (prefers-reduced-motion: reduce)` overrides the
      three duration tokens to `0ms`, sets `animation: none` and
      `transition: none` as a safety net.
- [ ] Route transition uses `fade-enter`: `App.tsx` keys the
      screen content on `section` and wraps with
      `className="fade-enter"`.
- [ ] Three grep guards in `tests/test_web_tokens.py`:
      the three classes are present; enter > exit for each;
      the reduced-motion block carries the 0ms override; and
      `App.tsx` keys the route mount.
- [ ] Each guard mutation-tested: removing the reduced-motion
      block, dropping the route key, or replacing an enter with
      an exit duration flips the corresponding guard red.
