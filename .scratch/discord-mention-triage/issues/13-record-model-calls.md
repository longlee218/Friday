# 13: Record every model call

**What to build:** What was sent to a model and what came back survives the process
that made the call, so "why did it classify that as an access request?" is a question
you can answer tomorrow rather than only while it happens.

**Blocked by:** 04

**Status:** ready-for-agent

`friday/llm_log.py` already captures both sides through the SDK's `on_llm_start` /
`on_llm_end` hooks, but writes them to DEBUG logs — lost on restart, and unreadable
from the board. The question this answers is always asked *after* the fact.

- [ ] The prompt, the system instructions, the model's output and any tool call it made are stored against the run that produced them
- [ ] Token counts are stored, so cost per decision is answerable
- [ ] A decision can be traced from the message that caused it to the model call that made it
- [ ] Stored calls are trimmed on a configured retention bound, so a long-running container does not fill its volume
- [ ] Nothing that reaches storage or logs contains a credential
