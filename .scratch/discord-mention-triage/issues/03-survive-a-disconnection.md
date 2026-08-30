# 03: Survive a disconnection

**What to build:** Messages that arrive while the connection is down are still captured
once it returns, and no message is ever captured twice. This is the reliability layer:
a live connection alone loses anything sent during an outage.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] A brief disconnection followed by a reconnect loses no messages
- [ ] An outage long enough to invalidate the session still recovers the missed messages via the periodic sweep
- [ ] A message delivered by both the live connection and the sweep produces exactly one stored event
- [ ] The sweep runs on a timer as well as on reconnect, so a stalled-but-open connection is still covered
- [ ] Resume state and per-channel position survive a process restart; the service does not re-process history from the beginning
- [ ] The sweep is scoped to whitelisted channels and does not fan out across everything visible
