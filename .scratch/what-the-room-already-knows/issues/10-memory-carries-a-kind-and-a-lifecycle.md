# 10: Memory carries a kind and a lifecycle

**What to build:** A memory says what kind of thing it is and whether it is
still current. Today it is free text with a soft delete, so a decision that
changed sits beside the one it replaced with nothing to say which is live, and
correcting a typo is indistinguishable from a reversal.

The table has zero rows, so the migration has no blast radius.

**Blocked by:** 01

**Decisions:** D2, D13, D14, D16, D17

**Status:** ready-for-agent

- [ ] A memory carries a kind, one of the five D14 names, and the reader follows
      from the kind rather than from a second field
- [ ] A memory carries a status and a link to whatever replaced it
- [ ] Correcting the wording of a memory is a different operation from replacing
      what it claims, and the two are named differently
- [ ] Every reader that serves a model reads only active rows
- [ ] The extractor reads the four domain kinds; the responder reads the voice
      kind and keeps its four tools
- [ ] Voice material written by the responder's own tools lands in the table
      under the voice kind — the split between the two stores is by who writes,
      not by kind (D13)
- [ ] The board shows what a superseded memory used to say and when it changed
- [ ] One migration; existing rows take the kind matching their reader today and
      active status
- [ ] Guards deleted once and watched go red
