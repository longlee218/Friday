"""Asking about *named fields*, when the fields are known in advance.

The sibling of `ask_clarification`, and deliberately not the same tool. That
one takes a question in words, which is right when nobody knows in advance
what might be unclear. This one takes a closed enum of one type's own askable
fields, so the model **cannot** name a field that does not exist — the same
property that makes `classify` safe.

Collapsing the two into one tool would trade a constraint the code can check
for a shorter list, which is the wrong direction: an extractor that invents a
field name asks the reporter a question about nothing.

A factory, because the enum differs per type. What is shared is the shape.
"""

from __future__ import annotations

from typing import Literal

from friday.agent.harness import ToolContext, tool
from friday.domain.models import Params, askable_fields
from friday.extraction.clarify import Clarify, FieldsCapture

__all__ = ["ask_for_fields_tool"]


def ask_for_fields_tool(params_cls: type[Params]):
    """Build `ask_for_fields` for one type: a closed enum of that type's
    own *askable* fields — see `askable_fields` in `friday.domain.models`,
    which is also what the guard on "every askable field says how to ask about
    it" reads, so the enum and that guard cannot mean different things. One
    function because every type needs the identical shape, differing only in
    which fields it may name.
    """
    askable = askable_fields(params_cls)
    FieldName = Literal[askable]

    def ask_for_fields(
        ctx: ToolContext[FieldsCapture], fields: list[FieldName], because: str
    ) -> str:
        ctx.context.clarify = Clarify(fields=tuple(fields), because=because)
        return "recorded"

    # `FieldName` is local to this call — `from __future__ import annotations`
    # stringifies the signature above, and resolving it back would eval that
    # string against the *module's* globals, where `FieldName` does not
    # exist. Setting the real object here bypasses that eval for this one
    # parameter.
    ask_for_fields.__annotations__["fields"] = list[FieldName]

    ask_for_fields.__doc__ = (
        "Ask the reporter for specific fields, because something in what "
        "they wrote makes this worth asking even though nothing here "
        "requires it structurally — an ambiguity, a detail the report "
        "implies but does not state.\n\n"
        "Args:\n"
        f"    fields: which of this type's own fields you mean — "
        f"{', '.join(askable)}.\n"
        "    because: why, in one short phrase — what you read that makes "
        "this worth asking."
    )
    return tool(ask_for_fields)
