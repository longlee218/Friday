"""Asking a model for a shape, and only believing what actually fits it.

Three things belong together here and were previously spread across two
modules that each did a different subset: finding the JSON in what a model
actually returns, checking it against a declared shape, and describing that
shape to the model in the first place. The shape is a dataclass, and it is
the **one** source for all three — the prompt text is generated from it, the
validation is against it, and the value handed back is an instance of it.

**Why validation lives here and not in `response_format: json_schema`.**
Measured against the configured provider (MiniMax-M3, 2026-09-11), not
assumed: it accepts `response_format: {"type": "json_schema", "strict":
true}` without complaint — no 400, which is what `docs/DESIGN.md` feared —
and then ignores it completely. The reply came back fenced in ```json, with
prose after it, and naming an enum member that was not in the enum. A
provider that rejects the parameter is a provider you find out about; one
that accepts and ignores it is one you do not, and the schema becomes a
comment that reads like a guarantee. So nothing here is sent on the wire:
the shape is described in the prompt, and every answer is checked in this
process before anybody is allowed to believe it.

The openai-agents SDK's own `output_type` is not usable for this. It emits
exactly that `response_format` envelope for Chat Completions and has no
prompt-only mode (`agents/models/chatcmpl_converter.py`'s
`convert_response_format`), `strict_json_schema=False` changes a boolean
inside the same envelope rather than the envelope, and its validation is
gated behind the same `is_plain_text()` flag as the wire format — so there
is no way to ask it for "validate locally, send nothing extra". Its tool
path *does* validate arguments client-side, with a retry, and that is
exactly the guarantee this module brings to the answers that are not tool
calls.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import fields as dataclass_fields
from typing import Any, Literal, get_args, get_origin, get_type_hints

from pydantic import TypeAdapter, ValidationError

__all__ = ["describe", "find_json", "fits"]

log = logging.getLogger(__name__)

def find_json(output: str) -> dict[str, Any] | None:
    """The JSON object in what a model returned, or `None` if there is none.

    Not `json.loads(output)`. Measured against the configured provider, a
    reply that *does* contain the right object arrives wrapped: a `<think>`
    block first, then a ```json fence, then prose explaining itself. Every
    one of those makes a bare `json.loads` raise, and the caller that did
    that (`channel_context._parse_summary`) stored the entire blob —
    reasoning included — as the room's topic, then rendered it into every
    later prompt for that room.

    Braces are matched rather than searched for: the last `}` in an output
    may belong to prose after the object, and the first `{` may open an
    example inside it.

    **Finding is not believing.** This says what shape the bytes are, never
    whether the values are right — that is `fits`, and nothing here should
    be used without it.
    """
    return _first_object(_without_reasoning(output))


def _without_reasoning(output: str) -> str:
    """What is left once the model's own working-out is removed.

    **Reasoning is a prefix, and only a prefix.** That is the rule, and it is
    narrower than the one this started with — which stripped a `<think>`
    wherever it appeared, to end of text if it was unclosed. An adversarial
    review found what that costs: `{"note": "he wrote <think> in chat"}`
    returned nothing at all, because the tail rule ate the reply that carried
    it. The extractor's whole job is copying a reporter's bytes out verbatim,
    so a `<think>` *inside a value* is ordinary content, not deliberation.

    Three shapes, because providers disagree about how they mark it:

    - a bare `</think>` with nothing opening it — a provider that strips the
      opening tag on the way out. Everything before it is reasoning, and
      leaving it in put the deliberation's own JSON in front of the answer.
    - a closed `<think>…</think>` at the front.
    - an unclosed `<think>` at the front: the budget ran out mid-thought and
      there is no answer after it at all.
    """
    text = output
    closing = text.find("</think>")
    if closing != -1 and "<think>" not in text[:closing]:
        text = text[closing + len("</think>") :]
    while text.lstrip().startswith("<think>"):
        text = text.lstrip()[len("<think>") :]
        closing = text.find("</think>")
        text = "" if closing == -1 else text[closing + len("</think>") :]
    return text.strip()


def _first_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    while start != -1:
        end = _closing_brace(text, start)
        if end is not None:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
            else:
                return data if isinstance(data, dict) else None
        start = text.find("{", start + 1)
    return None


def _closing_brace(text: str, start: int) -> int | None:
    """Where the object opened at `start` ends, or `None` if it never does.

    **String-aware**, which the scan this replaces was not: it counted every
    `{` and `}` byte, including the ones inside a string value. A *balanced*
    pair in a string survived that by luck — `-d '{"amount": 100}'` opens and
    closes — and an unbalanced one did not, which is a plain `curl` that was
    truncated when it was pasted. The extractor's whole job is copying a
    `curl` out verbatim, so a brace inside a string is not an edge case here,
    it is the payload.
    """
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def fits(data: dict[str, Any], schema: type) -> tuple[Any | None, str | None]:
    """`(instance, None)` when `data` fits `schema`, `(None, why)` when not.

    `why` is written for the model, not for a log: it is handed straight
    back on the repair turn, so it names the field and what was wrong with
    it rather than quoting a Python traceback.

    **An unknown key is dropped, not fatal**, which is the one tolerance
    kept from the parser this replaces — and kept for its recorded reason: a
    single invented or decorated key used to raise on the constructor and
    discard the whole extraction, including the fields that were read
    correctly. A *wrong-typed* known key is a different matter and is
    exactly what this function exists to catch: a dataclass constructor
    accepts `environment=["a","b"]` without complaint, and the error only
    surfaces later, somewhere that has no idea a model caused it.
    """
    known = {f.name for f in dataclass_fields(schema)}
    kept = {k: v for k, v in data.items() if k in known}
    unknown = sorted(set(data) - known)
    if unknown:
        log.info("ignoring %s the shape does not name", ", ".join(map(repr, unknown)))
    if data and not kept:
        # **An object whose keys are all unknown is a different shape, not an
        # empty answer**, and the difference is the whole point of this
        # module. Dropping them all leaves `{}`, every field of a `Params`
        # has a default, and the caller would be handed a successful
        # extraction of nothing — the exact failure the parser this replaces
        # was deleted for. `{"parameters": {...}}` is the shape that does
        # this: a model wrapping the object it was asked for. Found by an
        # adversarial review of this file, not by writing it.
        #
        # A literal `{}` is left alone: that is a model saying it found
        # nothing, which is an answer, and every field being absent is what
        # it means.
        return None, (
            f"none of the keys are ones this shape has — it names "
            f"{', '.join(sorted(known))}, and the reply had "
            f"{', '.join(unknown)}"
        )
    try:
        return TypeAdapter(schema).validate_python(kept), None
    except ValidationError as invalid:
        return None, "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'the object'}: {e['msg']}"
            for e in invalid.errors()
        )


def describe(schema: type, *, omit: Any = None) -> str:
    """The shape, written for the model, generated from the shape itself.

    One source of truth, and the reason this is not a hand-written paragraph
    beside the schema: `channel_context` used to carry the four summary keys
    in prose in `SUMMARY_JOB` *and* in `SUMMARY_FIELDS`, which is two
    encodings of one contract and the kind that drifts silently — the prose
    is what the model reads and the tuple is what the code enforces, so a
    fifth field added to one is invisible to the other.

    Each line is `- name: type — doc`, with `doc` read from the field's own
    `doc` metadata where it has one (the extraction schemas do; it is the
    same string that tells the extractor what a field means).
    """
    # Resolved rather than read off `field.type`, which `from __future__
    # import annotations` leaves as source text — and text is what the first
    # version of this matched on, so `int` stayed "int" while `str` became
    # "string" and `list[str]` became "list[string]" by the accident of
    # containing one. Found by this module's own tests, not by reading.
    try:
        hints: dict[str, Any] = dict(get_type_hints(schema))
    except NameError:
        # A schema whose annotations cannot be resolved from its module's
        # globals — one declared inside a function, against a locally aliased
        # import. Real schemas are module-level, but describing a shape is
        # prompt-building and must not be the thing that raises: fall back to
        # the source text `field.type` already carries.
        hints = {}
    lines = []
    for field in dataclass_fields(schema):
        # `omit` is an instance whose already-answered fields drop out of the
        # description — ticket 08's D8, which shrinks the schema an extractor
        # is shown once a field is filled. Truthy, not merely non-`None`: a
        # field a model once wrote `""` for is still blank, the same rule
        # `_fill` applies.
        if omit is not None and getattr(omit, field.name, None):
            continue
        # `field.metadata` is always a mapping — empty when nothing was set —
        # so the `or {}` this was written with is not a guard, it is what
        # makes the type of the expression `dict[Never, Never]`.
        doc = field.metadata.get("doc")
        annotation = hints[field.name] if field.name in hints else field.type
        rendered = f"- {field.name}: {_type_name(annotation)}"
        lines.append(f"{rendered} — {doc}" if doc else rendered)
    return "\n".join(lines)


def _type_name(annotation: Any) -> str:
    """What a field accepts, in the words a model needs rather than Python's.

    `str | None` is the shape of nearly every field here and "string or
    null" is what makes a model write `null` instead of inventing a value —
    which is the whole of what `MODEL_AUTHORED`'s absent fields rely on.
    """
    plain = {
        str: "string", int: "integer", float: "number", bool: "true or false",
        type(None): "null",
    }
    if isinstance(annotation, str):
        # The unresolved fallback above. Read as source text, which is all
        # there is: good enough to tell a model "string or null".
        #
        # **Whole words.** A plain `.replace("str", "string")` rewrites any
        # `str` it finds, including one inside a name the model is meant to
        # write back — `Literal["strict", "loose"]` came out as `stringict`,
        # which is a closed-set member nobody can name. Found by the test that
        # declares its schema inside a function, which is the case that
        # reaches this branch at all.
        rewritten = re.sub(r"\bOptional\[str\]", "string or null", annotation)
        rewritten = re.sub(r"\bstr\b", "string", rewritten)
        return rewritten.replace("| None", "or null").strip()
    if annotation in plain:
        return plain[annotation]
    origin = get_origin(annotation)
    if origin is Literal:
        # Quoted, and handled before the branch below rather than falling into
        # it. A `Literal`'s arguments are *values*, not types, so the generic
        # path recursed into them as annotations and ran the source-text
        # fallback's replacements over each one — which leaves
        # `correlation_id` alone by luck and would turn a member named
        # `strict` into `stringict`.
        return " or ".join(f'"{value}"' for value in get_args(annotation))
    if origin is not None:
        args = get_args(annotation)
        nullable = type(None) in args
        inner = " or ".join(_type_name(a) for a in args if a is not type(None))
        if origin in (list, set, tuple):
            return f"list[{inner}]"
        return f"{inner} or null" if nullable else inner
    return getattr(annotation, "__name__", str(annotation))
