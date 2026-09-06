# 06: The task screen — what it did, and what it cost

**What to build:** Tasks by state, and behind each one: which tools it reached
for, which prompts it sent, how long each took, how many attempts, how many
tokens.

**Blocked by:** 02, 05

**Decisions:** D11

**Status:** todo

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

- [ ] Tasks grouped by state, every `TaskState` present even when empty —
      an empty column is information, a missing one is a bug the operator
      cannot see
- [ ] One task expands to its calls in time order, model and tool interleaved,
      each showing agent, node, latency, attempt
- [ ] A model call shows its **full** prompt and output, not a preview. This is
      the debug view; truncation defeats it
- [ ] A tool call shows its arguments, its result, and whether it failed —
      `ToolCall.failed` exists precisely because a failure that reads as an
      answer is the one this board's ticket 07 was written for
- [ ] Token totals per task, and per agent for the day. `Database.spent_today`
      exists from ticket 03 of `nothing-runs-unmeasured` and has no route yet
- [ ] An attempt greater than 1 is visible without expanding anything — a
      retried call is the cheapest early sign a provider is struggling
- [ ] Long prompts scroll inside their own container; the page does not scroll
      sideways
- [ ] Built through the `ui-ux-pro-max` skill: contrast, focus rings, SVG icons
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
