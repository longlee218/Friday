# 13: Record every model call

**What to build:** What was sent to a model and what came back survives the process
that made the call, so "why did it classify that as an access request?" is a question
you can answer tomorrow rather than only while it happens.

**Blocked by:** 04

**Status:** done

`friday/llm_log.py` already captures both sides through the SDK's `on_llm_start` /
`on_llm_end` hooks, but writes them to DEBUG logs — lost on restart, and unreadable
from the board. The question this answers is always asked *after* the fact.

- [x] The prompt, the system instructions, the model's output and any tool call it made are stored against the run that produced them
- [x] Token counts are stored, so cost per decision is answerable
- [x] A decision can be traced from the message that caused it to the model call that made it
- [x] Stored calls are trimmed on a configured retention bound, so a long-running container does not fill its volume
- [x] Nothing that reaches storage or logs contains a credential


## Delivered

`model_calls` holds the system prompt, the assembled prompt, the output
including any tool call, and both token counts — keyed on the message the call
was made about, so the trail runs message → decision → the prompt behind it.

**Triage still writes nothing.** That is a ticket 04 criterion with a test
holding it, so `LogHooks` collects rather than stores: `decide(..., calls=[])`
hands the caller both sides of every call, and `TriageRunner` is what turns
them into rows. The model name recorded is the one from configuration, not
whatever object the SDK wrapped it in — what matters later is which model was
asked.

**Retention rides the heartbeat.** One indexed delete on the only loop already
running on a timer, rather than a fifth loop to own it.

## Redaction

`friday/redact.py` scrubs anything token-shaped, and is attached to the root
log **handler** rather than to our loggers — the realistic leak is a library
raising an exception that carries an Authorization header, not our own code
printing a token deliberately. The same scrub runs on prompts and outputs
before they are stored, and on the exception text in a `NeedsHuman` reason,
which is the one place a provider error reaches the database.

Deliberately broad: a false positive costs a few unreadable characters in a log
line, a false negative costs the account. Checked that a `correlationId` is
*not* caught — it is a required task parameter, and redacting one would break
the workflow that asks for it.
