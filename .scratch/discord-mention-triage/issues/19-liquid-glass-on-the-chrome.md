# 19: Liquid Glass, on the chrome only

**What to build:** The board gets the Liquid Glass visual language — on its frame.
The parts that carry information stay opaque and legible, and the failure list stays
the loudest thing on the page.

**Blocked by:** 18

**Status:** ready-for-agent

The restriction is not taste, it is the page's job. This board exists to make a
failure obvious, and translucency makes contrast a function of whatever happens to
be behind it.

Apple reached the same conclusion on their own material. iOS 26 betas were measured
at 1.5:1 against a 4.5:1 requirement, the American Foundation for the Blind wrote
publicly in December 2025, and iOS 27 raised the transparency floor from roughly 40%
to 60% opacity and shipped a high-contrast mode that halves the blur and puts text
back on opaque backing. They retreated on precisely the axis this page depends on.

Worth knowing before starting: what separates Liquid Glass from 2020-era
glassmorphism is *refraction* — content behind is lensed, not merely blurred — and on
the web that needs an SVG displacement map, which is **Chromium only**. Motion-tracked
highlights are not reproducible on desktop at all. Whatever is built has to look
finished in Safari and Firefox without them.

- [ ] Page chrome, status pills and hover states use the material; the failure list, error text, prompt bodies and token counts do not
- [ ] Text on any glass surface meets WCAG AA, checked against the lightest and darkest thing that can pass behind it — not the average
- [ ] The page is legible with no support for the effect at all, and honours a request for reduced transparency
- [ ] Refraction, where used, degrades to plain translucency rather than disappearing
- [ ] The page does not become slower to repaint on a board that refreshes every few seconds
- [ ] Both colour schemes are complete on their own, not one derived from the other by inversion
