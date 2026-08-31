# 09: Liveness signals

**What to build:** The human finds out when the service has stopped receiving messages,
rather than mistaking silence for a quiet day.

**Blocked by:** 03, 06

**Status:** done

- [x] A connection down for longer than the configured threshold triggers a direct message to the human
- [x] The alert is not repeated on every check while the connection remains down
- [x] Recovery is communicated, so the human knows the gap has closed
- [x] A daily summary reports how many mentions were captured and how many became tasks
- [x] The web page reflects the same connection health


## Delivered

Nothing tracked the connection at all. `reconnected` was set on ready and
resumed and never cleared by the provider, so a dead gateway and a quiet channel
produced the same observable: no messages. The provider now records
`down_since`, and the **first** disconnect is the one that counts — `discord.py`
fires `on_disconnect` on every reconnection attempt, and taking the latest would
reset the clock forever and the alert would never fire.

Below the threshold nothing is said. Discord drops and resumes constantly, and
alerting on a blip is how an operator learns to ignore the alert that matters.
Above it, once — and coming back is said too, because otherwise the operator is
left believing it is still down and acting on that.

## Alerts go through the outbox

They retry, and one that could not be delivered appears on the board rather than
vanishing — which would be a peculiar way for a liveness system to fail.

That required `outbox.task_id` to become nullable: an alert is about the system,
not about work. The selection join is now an **outer** join, or a row with no
task would be silently dropped by the very query meant to release it. The failure
path also had to stop assuming a task exists before moving one to `needs_human`.

## Still one gap, and it is deliberate

The daily summary reports what is *held*, not what happened in the last day —
`counts()` is a snapshot. "How many mentions were captured today" needs counting
by date, and the number that matters more on a quiet day is the one that says
the process is alive at all. Worth sharpening when there is a day's traffic to
sharpen it against.
