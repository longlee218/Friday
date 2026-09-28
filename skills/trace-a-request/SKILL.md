---
name: trace-a-request
description: Find the log lines for one request, given a correlationId
---

# Tracing one request

You need a correlationId, or a curl the reporter ran. An environment narrows
the search but cannot locate anything on its own.

1. Query the log store for the id, over the last hour. Widen to a day only if
   the first window is empty — a wide window on a busy service returns more
   than fits in a reply.
2. Read from the **oldest** line forward. The first error is usually the cause;
   everything after it is the system reacting to that cause.
3. If the trace names a file, the topmost *application* frame is the one that
   matters. Framework frames tell you how it got there, not why it broke.

If the logs are empty, say so rather than guessing. An empty window usually
means the id is wrong, the environment is wrong, or the request never reached
us — and which of those it is, is worth asking about.
