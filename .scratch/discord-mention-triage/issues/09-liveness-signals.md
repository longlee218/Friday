# 09: Liveness signals

**What to build:** The human finds out when the service has stopped receiving messages,
rather than mistaking silence for a quiet day.

**Blocked by:** 03, 06

**Status:** ready-for-agent

- [ ] A connection down for longer than the configured threshold triggers a direct message to the human
- [ ] The alert is not repeated on every check while the connection remains down
- [ ] Recovery is communicated, so the human knows the gap has closed
- [ ] A daily summary reports how many mentions were captured and how many became tasks
- [ ] The web page reflects the same connection health
