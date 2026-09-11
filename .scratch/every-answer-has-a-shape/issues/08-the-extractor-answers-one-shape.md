# 08: The extractor answers one shape, what it wants to ask about included

**What to build:** an extraction is one validated object — that task type's own
parameters, plus which of its fields the extractor wants to ask about and why
(D7). The separate tool and the capture it wrote into are deleted. The fields it
may name are still checked against the fields that type actually has, which the
tool's enum gave for free and which must not be lost in the move: an extractor
that invents a field name asks the reporter a question about nothing.

**Blocked by:** 05.

**Status:** ready-for-agent

- [ ] An extraction and its request for more detail arrive as one validated object.
- [ ] A wrong-typed field is refused rather than constructed.
- [ ] A request naming a field the type does not have is refused.
- [ ] An extraction that produced nothing is not remembered as an answer.
- [ ] The turn budget still covers a correction and a skill fetch, or the budget is changed deliberately on the evidence.
