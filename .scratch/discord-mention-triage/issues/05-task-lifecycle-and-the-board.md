# 05: Task lifecycle and the board

**What to build:** Tasks move through defined states, and a web page shows every task
grouped by the state it is in.

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] Tasks are moved by named operations; no caller writes a state value directly
- [ ] An illegal state change is rejected rather than silently applied
- [ ] Tasks left mid-flight by a restart are recovered on startup rather than stranded
- [ ] The page lists every task grouped into its five states
- [ ] The page updates without a manual refresh
- [ ] The page shows connection health and the time of the most recent captured event
- [ ] The page offers no way to change anything — it displays state only
