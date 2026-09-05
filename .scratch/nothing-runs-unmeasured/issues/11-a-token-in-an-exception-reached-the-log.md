# 11: A token in an exception reached the log

**What to build:** `Redacting` scrubs an exception passed as a log argument,
and the traceback behind `exc_info`. Both went through untouched.

**Blocked by:** None

**Decisions:** None — this is the module doing what it already said it did

**Status:** done

## Why

`friday/ops/redact.py`'s own opening paragraph names the case: *"The realistic
leak is not someone printing it on purpose — it is an unhandled exception
carrying a request header, or a provider error quoting the Authorization line,
landing in a log file."*

That case was not covered. `Redacting.filter` scrubbed `record.msg` and string
arguments; an exception is neither. `_scrub_one` returned the object as it
found it, and the formatter called `str()` on it afterwards — after every
filter has run. `record.exc_info` was never looked at at all.

Measured, one record through the filter and a real formatter:

```
plain message with [REDACTED]
classify failed: {"x":1} provider rejected: Bearer abcdefghijklmnop0123456789ABCDEF
classify failed: provider rejected: Bearer abcdefghijklmnop0123456789ABCDEF
Traceback (most recent call last):
RuntimeError: provider rejected: Bearer abcdefghijklmnop0123456789ABCDEF
```

The first line is what the filter was written for. The other three are the
shapes a library actually uses — and one of them is in the path this codebase
now runs on every failed tool call: `agents/tool.py`'s `_on_handled_error`
logs `logger.error("%s failed: %s %s", prefix, input_json, error,
exc_info=error)` at ERROR, with the model's raw arguments beside it. That runs
whether or not `failure_error_function` returns something friendly, so ticket
10's work made this path busier without making it safer.

Found by the subagent review of ticket 10, while disputing a sentence in
`harness.py` that claimed the exception was "logged here, scrubbed". It was
logged twice, and neither copy was scrubbed.

## Acceptance criteria

- [x] An exception passed as a log argument is rendered and scrubbed; every
      other non-string type is left alone, since a `%d` handed a string is a
      formatting error this must not invent
- [x] `record.exc_text` is filled with a scrubbed rendering when `exc_info` is
      set, which is how a filter reaches something the formatter renders after
      it
- [x] Both are driven through a real `Formatter`, not by inspecting the
      record — the record is not what reaches the file
- [x] The traceback is still readable afterwards
- [x] Both guards deleted once and watched go red

## What it came to

Two branches. `_scrub_one` renders a `BaseException` through `scrub`, and the
filter fills `exc_text` from `scrubbed_traceback` — a function this module
already had, written for `sys.excepthook`, which turned out to be exactly what
the logging path needed too.

`exc_text` is the slot a `Formatter` caches its rendered traceback in and
checks before rendering its own, so writing it from a filter is the supported
way to reach text that is otherwise produced after every filter has run.

Worth keeping in view: `scrub` matches four credential shapes and nothing
else. It does not redact paths, hostnames or message bodies, and a traceback
that is now scrubbed is not thereby harmless. This ticket closes the gap
between what the module claimed and what it did; it does not widen what the
module claims.

679 tests pass (677 before, +2).
