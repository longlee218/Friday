# 05: Task lifecycle and the board

**What to build:** Tasks move through defined states, and a web page shows what the
system is doing — tasks by state, messages as they arrive, model calls with their
prompts, and anything that failed to send.

It is a **debug view**, not a control panel. It displays; every action happens in
Discord. That is what lets it run without auth.

**Blocked by:** 04, 12, 13

**Status:** ready-for-agent

- [ ] Tasks are moved by named operations; no caller writes a state value directly
- [ ] An illegal state change is rejected rather than silently applied
- [ ] Tasks left mid-flight by a restart are recovered on startup rather than stranded
- [ ] The page lists every task grouped into its five states
- [ ] The page shows messages as they arrive, and the model calls made about them with their prompts and tool calls
- [ ] The page shows outbound rows that failed to send, including their text, so it can be copied and sent by hand
- [ ] The page updates without a manual refresh
- [ ] The page shows connection health and the time of the most recent captured event
- [ ] The page offers no way to change anything — it displays state only
