# 05: A model answers through a tool built from its own shape

**What to build:** one entry point on the harness that asks for a shape and hands
back an instance of it, or `None` (D1). The shape is a dataclass and the only
declaration: the prompt text is generated from it, the tool's parameter schema is
generated from it, and the arguments are validated against it in this process
(D2, D3, D5). A malformed call goes back to the model as the tool's own output
and `max_turns` decides how many corrections it gets — no second retry loop (D4).

The first caller is the summariser (D11), because its answer already has a shape
and it is the lowest-risk path to prove the mechanism on. String-scraping stays
as the fallback for a provider without usable tool calling (D13).

Two mechanics came out of a prototype and decide the design, so they are written
down rather than rediscovered:

```python
# The tool RETURNS the problem; it must not raise. A raise is wrapped in
# UserError and the whole run fails — the model never gets to correct it.
async def invoke(ctx, args: str):
    try:
        return adapter.validate_python(json.loads(args))
    except (json.JSONDecodeError, ValidationError) as bad:
        return f"that did not fit: {bad}"

# "Answered" means the tool returned an instance, not that it returned
# anything — the same distinction `stop_when` already draws, for the same
# reason: an error string is a tool output too.
def is_final(ctx, results):
    out = results[-1].output
    if isinstance(out, schema):
        return ToolsToFinalOutputResult(is_final_output=True, final_output=out)
    return ToolsToFinalOutputResult(is_final_output=False)
```

**Blocked by:** 04.

**Status:** done

- [x] An answer that fits comes back as an instance and costs one call.
- [x] An answer that does not fit is corrected once, carrying the reason, and then the caller gets `None`.
- [x] No usable answer is `None` and never an all-defaulted instance.
- [x] A failed call is not quoted back to the model.
- [x] The summariser's answer arrives and is validated this way, and a room's stored summary can no longer contain a reasoning block.
- [x] Driven through the scripted model transport and a real harness — not a double with its own copy of the mechanism.

## Comments

**The shape is declared at construction, not passed per call**, which is a
deviation from D1’s wording and not from its substance. An agent’s answer shape
does not vary between calls — the summariser always answers a `RoomSummary`,
each extractor always its own type’s `Params` — so `Harness(answers=...)`
builds the tool, its terminator and its `tool_choice` once rather than on every
call, and puts the contract where the rest of the agent’s wiring is.

Three things the prototype settled and the code now records:

- The tool body **returns** its problem. A raise inside `on_invoke_tool` is
  wrapped in `UserError` and fails the whole run.
- "Answered" means the tool returned an **instance**. An error string is a tool
  output too, which is the mistake that once made the SDK’s "try again with
  valid JSON" triage’s final answer.
- The instance is read off the run’s **items**, not `result.final_output`. The
  SDK does `if agent.output_type is None or is str: final_output =
  str(final_output)`, so an instance would reach the caller as its own repr.
  The fix the SDK intends — `output_type=schema` — is the one thing this design
  may not do, because that is what emits the `response_format` envelope D5
  forbids.

**One run, not two.** The correction is a turn inside the run now, so a
structured call is one `timeout_seconds` again rather than two. CLAUDE.md’s
one-run bound is true once more.

**A shape’s own docstring does not go on the wire.** Pydantic puts it on the
object as `description`, and `RoomSummary`’s runs to nine paragraphs about why
it has four fields and not six.

**The answer tool is the one tool outside `friday/tools/`.** It cannot live
there — `FunctionTool` comes from the SDK and `harness.py` is the only module
that may import it — and it is not a capability. `tests/test_tools.py` asserts
there is exactly one such function in that file and that it is this one, so the
exemption cannot quietly grow a second occupant.

The summariser is the first caller. The extractor declares its shape here too,
so the suite stays green; merging its clarification in is ticket 08.
