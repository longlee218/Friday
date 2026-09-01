---
name: where-to-find-a-correlation-id
description: How a reporter finds the correlationId (or what to send instead) when asked for one
---

# Where the correlationId is

Written for the person being asked, not for the agent. When you ask someone for
a correlationId and they do not know what that is, this is what to tell them —
in one line, in their message, not as a lecture.

**Where it is:**

- In the API response, header `x-request-id` (some services: `x-correlation-id`).
- In the browser: DevTools → Network → click the failed request → Headers.
- In Postman / Insomnia: the response's Headers tab.
- If they only have an error page or a screenshot, it is usually printed at the
  bottom as `Request ID` or `Trace ID`.

**What to send instead, if they cannot find it:**

- The `curl` they ran (Postman: Code → cURL). That is enough to trace on its own.
- Failing both: the exact time it happened and which environment, and the
  operator will look — but say plainly that this is slower.

**How to say it:** one sentence, the shortest route that fits what they have.
Someone on a browser gets the DevTools line; someone who mentioned Postman gets
the Postman line; nobody gets all four. If they have already said they have a
curl, do not explain correlationIds at all — ask for the curl.
