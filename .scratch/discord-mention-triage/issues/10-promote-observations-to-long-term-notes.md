# 10: Promote observations to long-term notes

**What to build:** Observations recorded during work become durable notes the system
reuses — but only the ones whose task the human approved, so a wrong guess made under
pressure does not become a permanent belief.

**Blocked by:** 06, 08

**Status:** ready-for-agent

- [ ] Observations from an approved task are promoted into the long-term notes
- [ ] Observations from a task that was never approved are not promoted
- [ ] Promotion rules differ by category, so categories needing corroboration are treated differently from those that do not
- [ ] Promoted entries track how many approved tasks support them
- [ ] Long-term notes stay within the configured size limit
- [ ] The notes file is rewritten rather than appended to, so it stays stable between promotions
- [ ] Long-term notes appear in the stable, early portion of each prompt
- [ ] Staged observations are cleared once promotion has considered them
