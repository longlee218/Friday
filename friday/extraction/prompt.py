"""What an extractor's prompt looks like, and from what it is assembled.

The stable half is one job text shared by every extractor — copy verbatim,
null beats a guess — because the extractors differ only in which fields they
fill. The per-call half is those fields' own descriptions, read off the params
class, then everything the reporter said. Schema first, so the model knows
what to look for before it reads what to look in.
"""

from __future__ import annotations

from friday.domain.models import Params

__all__ = ["build_input", "build_instructions"]

#: The job. "Reply in JSON only, with the schema fields as keys" is a contract
#: with `_parse` in this package — reworded freely, but the JSON promise stays.
INSTRUCTIONS = """You fill structured fields from what someone wrote.

You are shown the field schema — the names and what each one is for — and
everything the reporter has said about this, oldest first. The answer to a
question they were asked is in there as an ordinary later message, so read all
of it, not only the first line.

For every field, copy the matching value verbatim. Pass null when the value is
genuinely absent — never invent one, and never paraphrase a field that asks for
a literal value. A wrong correlationId sends someone looking through the wrong
request; a null one costs a question.

You are the only thing that reads this message for what it contains. Nothing
produced these fields before you and nothing corrects them after, except a
check that a value you did supply has the right shape.

If something is worth asking the reporter about — an ambiguity, a detail the
report implies but does not state — call ask_clarification with which fields
you mean and why. That is separate from filling fields: do both when both
apply, and still reply in JSON for whatever you did find.

Reply in JSON only, with the schema fields as keys."""


def build_instructions() -> str:
    return INSTRUCTIONS


def build_input(text: str, params_cls: type[Params]) -> str:
    """The field schema, then the reporter's words.

    Each field's meaning is its `doc` metadata on the params class — the field
    and its meaning live on the same line there, so they cannot drift apart.
    This renders them; it does not define them.
    """
    schema_lines = []
    for f in params_cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
        doc = (f.metadata or {}).get("doc", f.name.replace("_", " "))
        schema_lines.append(f"- {f.name}: {doc}")
    schema = "\n".join(schema_lines) or "(no fields)"
    return (
        "Fill every field below. Pass null when the value is genuinely "
        "absent — never invent one.\n\n"
        f"Fields:\n{schema}\n\n"
        f"What they said:\n{text}"
    )
