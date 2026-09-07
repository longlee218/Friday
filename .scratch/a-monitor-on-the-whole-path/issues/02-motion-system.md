# 02: Motion system

**What to build:**

`web/src/motion.css` defines three named motions (D2): `fade`,
`slide`, `scale`. Each is a keyframe + class pair plus a `data-enter` /
`data-exit` mechanism for transitions triggered by route change.

A `<MotionProvider>` (or hook) wraps the app so that
`prefers-reduced-motion: reduce` collapses every duration to 0ms and
turns every `transform` animation into an opacity-only fade. The test
asserts the reduced-motion path is wired — a unit test toggling
`matchMedia` and asserting computed style durations.

**Blocked by:** 01.

**Decisions:** D2.

**Status:** ready-for-agent
