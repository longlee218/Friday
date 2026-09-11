# 08: The extractor answers one shape, what it wants to ask about included

**What to build:** an extraction is one validated object — that task type's own
parameters, plus which of its fields the extractor wants to ask about and why
(D7). The separate tool and the capture it wrote into are deleted. The fields it
may name are still checked against the fields that type actually has, which the
tool's enum gave for free and which must not be lost in the move: an extractor
that invents a field name asks the reporter a question about nothing.

**Blocked by:** 05.

**Status:** done

- [x] An extraction and its request for more detail arrive as one validated object.
- [x] A wrong-typed field is refused rather than constructed.
- [x] A request naming a field the type does not have is refused.
- [x] An extraction that produced nothing is not remembered as an answer.
- [x] The turn budget still covers a correction and a skill fetch, or the budget is changed deliberately on the evidence.

## Comments

`friday/extraction/clarify.py` is `friday/extraction/answer.py`, and holds
`answer_shape(params_cls)` — the type’s own parameters plus `ask_about` and
`because`, generated once per type and cached. The cache is load-bearing:
`build_extractor` and the run’s terminator both compare classes.

**Flat, not nested.** A `{"params": {...}, "ask_about": [...]}` wrapper would
be one less generated class and would tell the model less — a field named
`params` of type `ApiIssueParams` describes nothing — and would turn an
all-unknown-keys object into an empty answer, which is exactly what `fits`
refuses.

**The closed set survived the move**, which was the one thing worth losing
sleep over: `ask_about` is a `Literal` of the type’s askable fields, and it is
*checked* rather than only declared, since the provider is measured to ignore a
schema it has just been given.

**The tool’s parameters do not shrink as fields fill in**, where the prompt’s
field list does (D8 of the other board). That is the shape cache doing its job,
and what D8 bought is still bought — the prompt is what the model is told to
fill and what `input_fingerprint` hashes. Written down in
`friday/extraction/prompt.py` so the next reader does not have to work it out.

**Two real bugs in `describe` found on the way, both by mutation.** A
`Literal`’s arguments are values, not types, and the generic branch recursed
into them as annotations; and the source-text fallback replaced `str` as a
*substring*, so `Literal["strict", "loose"]` was described to the model as
`stringict` — a member of a closed set that does not exist.

**The turn budget was the spec’s named risk, so it is measured**: three model
calls in one run — reach for a skill, answer wrongly, answer again — all fit.

`clarification_system` takes *how* an agent asks rather than which tool it
calls. "Ask by calling `ask_about`" would have been an instruction to call
something that is a field.

## Review

`/code-review` against `b61a7e1`, Standards and Spec as parallel subagents.
Ten findings, every one verified against the code before acting — see the
commit `Review fixes: ten findings from the two-axis review`. Four were real
defects: a construction-time `TypeError` that would have been a boot loop when
an agent declared both `answers=` and its own `tool_choice`; the responder
losing its guarantee that a memory it writes is attributed to the responder;
the run's state not travelling at all when an agent had no memory tools; and
the tools-package exemption matching by basename rather than by path. D8's
recording-sink half had not been done. Each fix carries a test, deleted once
and watched go red.
