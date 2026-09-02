"""What an extractor's prompt looks like, and from what it is assembled.

The stable half is one job text shared by every extractor — copy verbatim,
null beats a guess — because the extractors differ only in which fields they
fill. The per-call half is those fields' own descriptions, read off the params
class, then everything the reporter said. Schema first, so the model knows
what to look for before it reads what to look in.
"""

from __future__ import annotations

from friday.agent.prompts import prompt
from friday.domain.models import Params

__all__ = ["build_input", "build_instructions"]


def build_instructions() -> str:
    return prompt("extractor")


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
