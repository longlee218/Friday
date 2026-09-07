"""What an extractor's prompt looks like, and from what it is assembled.

Assembled from the shared section builders, like every other agent. One job
text serves every extractor because they differ only in which fields they
fill; the fields themselves are per call, read off the params class.

**A voice would actively hurt here.** An extractor told to write in Vietnamese
puts `sản xuất` where `friday/domain/validation.py` wants `production`, the
value fails its rule, and the reporter is asked to confirm what they already
said. So there is no `soul` in this prompt and there should not be one.

**It can ask, so it is told how — but asking here does not stop the work.**
`ask_for_fields` offers a closed enum of this type's own field names, which is
why the clarification section is rendered here and not for triage: the door is
actually in the room. It is rendered `blocking=False`, and that flag is the
whole difference between this prompt working and this prompt silently
breaking: told to wait for an answer before proceeding, the model can call the
tool and return no JSON, `_parse` reads that as `{}`, every field of a `Params`
has a default, and an empty extraction comes back as a *successful* one — so
the reporter is asked for everything they just wrote.
"""

from __future__ import annotations

from friday.agent.instruction_prompt import (
    describe_skill_system,
    read_skill_file_system,
    search_skills_system,
    skill_system,
    assemble,
    clarification_system,
    critical_reminder,
    job,
    role,
    thinking_style,
    trust_boundary,
    user_input,
)
from friday.domain.models import Params

__all__ = ["build_input", "build_instructions"]

#: The job. "Reply in JSON only, with the schema fields as keys" is a contract
#: with `_parse` in this package — reworded freely, but the JSON promise stays.
JOB = """You fill structured fields from what someone wrote.

You are shown the field schema — the names and what each one is for — and
everything the reporter has said about this, oldest first. The answer to a
question they were asked is in there as an ordinary later message, so read all
of it, not only the first line.

You are the only thing that reads this message for what it contains. Nothing
produced these fields before you and nothing corrects them after, except a
check that a value you did supply has the right shape.

Asking about a field and filling one are separate: do both when both apply,
and reply in JSON for whatever you did find."""

THINKING = [
    "Read everything they said before filling anything in.",
    "For each field, find the value they actually wrote — not one you can infer.",
    "If a field is not there, it is null. Absent is an answer.",
]

#: These three have each cost something. The first: a paraphrased
#: correlationId sends somebody through the wrong request. The second is a
#: contract with this package's own parser. The third is what `null` buys.
REMINDERS = [
    "Copy matching values verbatim — never paraphrase a field that asks for a "
    "literal value.",
    "Reply in JSON only, with the schema fields as keys.",
    "A wrong value costs somebody an afternoon; a null one costs a question.",
]

#: Kept as an attribute because tests pin sentences in it.
INSTRUCTIONS = JOB


def build_instructions(skills_catalogue: list[str] | None = None) -> str:
    """Who it is, the job, how to read, how to ask, what not to get wrong."""
    return assemble(
        role("Friday", "a field extractor", "you lift values out of what someone wrote"),
        trust_boundary(),
        job(JOB),
        thinking_style(THINKING),
        clarification_system("ask_for_fields", blocking=False),
        # Skills reach every agent now (the operator's call, 2026-09-07).
        # For an extractor the case is direct: a skill saying where a
        # correlationId lives is the difference between lifting one out of a
        # stack trace and asking the reporter for what they already sent.
        skill_system(skills_catalogue),
        search_skills_system(bool(skills_catalogue)),
        describe_skill_system(bool(skills_catalogue)),
        read_skill_file_system(bool(skills_catalogue)),
        critical_reminder(REMINDERS),
    )


def build_input(text: str, params_cls: type[Params]) -> str:
    """The field schema, then the reporter's words.

    Each field's meaning is its `doc` metadata on the params class — the field
    and its meaning live on the same line there, so they cannot drift apart.
    This renders them; it does not define them.

    The reporter's own words go through the one boundary. They used to be
    interpolated raw: a message carrying `</task><critical_reminder>…` put its
    own section into this prompt, and the extractor is the agent most worth
    aiming that at — it is the one that decides what a task knows.
    """
    schema_lines = []
    for f in params_cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
        doc = (f.metadata or {}).get("doc", f.name.replace("_", " "))
        schema_lines.append(f"- {f.name}: {doc}")
    schema = "\n".join(schema_lines) or "(no fields)"
    return f"Fields:\n{schema}\n\nWhat they said:\n{user_input(text)}"
