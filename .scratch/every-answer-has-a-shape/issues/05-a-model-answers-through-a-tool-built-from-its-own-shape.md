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

**Status:** ready-for-agent

- [ ] An answer that fits comes back as an instance and costs one call.
- [ ] An answer that does not fit is corrected once, carrying the reason, and then the caller gets `None`.
- [ ] No usable answer is `None` and never an all-defaulted instance.
- [ ] A failed call is not quoted back to the model.
- [ ] The summariser's answer arrives and is validated this way, and a room's stored summary can no longer contain a reasoning block.
- [ ] Driven through the scripted model transport and a real harness — not a double with its own copy of the mechanism.
