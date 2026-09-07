# 06: The task screen — what it did, and what it cost

**What to build:** Tasks by state, and behind each one: which tools it reached
for, which prompts it sent, how long each took, how many attempts, how many
tokens.

**Blocked by:** 02, 05

**Decisions:** D11

**Status:** done

## Why

The operator's third request, and the one closest to what already exists —
`/api/tasks` and `/api/tasks/{id}/model-calls` both serve today, and
`/api/tasks/{id}/model-calls` returns the full `asdict`, so `node`,
`latency_ms` and `attempt` already come through.

What is new is the pairing. A task's *model* calls have been reachable since
ticket 02 of `nothing-runs-unmeasured`; its *tool* calls have never had a JSON
route at all — ticket 07 of that board recorded them and the only reader was
the HTML board that ticket 01 deletes. Seeing "it called `search_skills`, got
nothing, then asked the reporter" requires both, side by side, in time order.

The stated purpose is narrow and worth keeping narrow: **debug one case, and
see what it cost.** Not a metrics platform. The two questions this screen
exists to answer are "why did it do that" and "what am I paying for" — the
first is why the prompts must be readable in full, the second is why tokens are
summed rather than merely listed.

## Acceptance criteria

- [x] Tasks grouped by state, every `TaskState` present even when empty —
      an empty column is information, a missing one is a bug the operator
      cannot see
- [x] One task expands to its calls in time order, model and tool interleaved,
      each showing agent, node, latency, attempt — **false when first ticked**,
      and a review found it: `TaskCard` fetched `/api/tasks/{id}/model-calls`
      and nothing else, because there was no per-task tool route in the API at
      all. `/api/tasks/{id}/calls` serves both now and the screen interleaves
      them on `created_at`
- [x] A model call shows its **full** prompt and output, not a preview. This is
      the debug view; truncation defeats it
- [x] A tool call shows its arguments, its result, and whether it failed — on
      the flow screen from the start, and on *this* screen only after the
      missing route above was added —
      `ToolCall.failed` exists precisely because a failure that reads as an
      answer is the one this board's ticket 07 was written for
- [x] Token totals per task, and per agent for the day. `Database.spent_today`
      exists from ticket 03 of `nothing-runs-unmeasured` and has no route yet
- [x] An attempt greater than 1 is visible without expanding anything — a
      retried call is the cheapest early sign a provider is struggling
- [x] Long prompts scroll inside their own container; the page does not scroll
      sideways
- [x] Built through the `ui-ux-pro-max` skill: contrast, focus rings, SVG icons
      rather than emoji, real timings

## Notes

`spent_today` needs a route; decide whether it belongs on this screen's
aggregate or as its own. Keep the shape consistent with `/api/board` — one
request for what one screen renders (D6).

The `daily_token_budget` knob in `config.yaml` is unset by default and this
screen is how an operator would ever learn what to set it to. Worth showing the
day's spend against the configured ceiling when there is one, and against
nothing when there is not — an empty ceiling is not an error state.

Nothing here is editable. Task state, approvals and classifications stay
decided in Discord (D7).

## What it came to

`spent_today` had no route and this ticket's own criterion asked for one, so
`GET /api/spend` exists — total and per agent. Per agent is grouped in one
query (`Database.spent_today_by_agent`) rather than asked once per name,
because there is no list of names to ask for: which agents exist is
`config.yaml`'s business. An agent
that spent nothing today is absent rather than zero, which is the same answer
and does not require knowing it exists.
