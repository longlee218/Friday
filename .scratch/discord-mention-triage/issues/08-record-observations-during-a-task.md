# 08: Record observations during a task

**What to build:** A reasoning step can note something it learned while working, so the
observation is not lost when the step ends. Nothing it writes influences anything yet.

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] A step can record an observation supplying only a category and text
- [ ] The recorded entry carries the originating task and a timestamp, attached by the runtime rather than supplied by the model
- [ ] An observation in an unrecognised category is rejected
- [ ] Recorded observations are scoped to their task and do not appear in any prompt
- [ ] Observations survive a restart
