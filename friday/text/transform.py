"""What someone typed, turned into something worth reading.

A Discord message is not text. It is prose, and fenced code, and a screenshot,
and a decorative emoji, and whatever whitespace the client inserted — and every
one of those wants different handling. Passing the lot through as one string
means the model reads padding as content, and it means an attachment is not
read at all.

**Split before cleaning.** This is the whole difficulty. Stripping emoji or
collapsing whitespace inside a `curl` command or a stack trace corrupts the one
thing in the message that has to survive verbatim — the extractor is looking
for exactly that, and a `curl` with its newlines eaten is not a curl. So code
comes out first and is never touched; only the prose around it is cleaned.

This does not decide anything. It has no opinion on what the message is, no
model, and no access to anything. Deciding is `friday/triage/`; refusing to
send something is `friday/triage/prefilter.py`, which is a different concern
with a different reason — pay talk must not reach a third-party API at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Attachment", "Cleaned", "render_attachments", "transform"]


@dataclass(frozen=True, slots=True)
class Attachment:
    """A file someone posted. Named, not fetched.

    The name and type are what a reader needs to know it exists — "there is a
    screenshot here" changes what a question is worth asking. Downloading it is
    a separate decision with a separate cost, and nothing does it yet.
    """

    filename: str
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class Cleaned:
    """The result. Prose and code kept apart, because they are read apart."""

    #: The whole message: prose cleaned, code untouched and still in place.
    #: This is what gets stored and shown — a `curl` lifted out of the text
    #: and kept only here would be a `curl` nothing downstream can find, and
    #: finding it is the point.
    text: str
    #: Every fenced or inline code span, verbatim and in order. A `curl`, a
    #: stack trace, a JSON payload — the things a value is lifted out of.
    code: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.text or self.code)


#: Fenced blocks first, then inline spans. Fenced wins because an inline
#: pattern would otherwise chew through a fence's own backticks.
_FENCED = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")

#: Emoji, and the joiners and selectors that glue them together. Deliberately
#: enumerated rather than "anything above the BMP": Vietnamese is full of
#: combining diacritics and a broad sweep would eat them.
_EMOJI = re.compile(
    "["
    "\U0001f300-\U0001faff"  # pictographs, transport, symbols, extended-A
    "\U0001f000-\U0001f0ff"  # mahjong, dominoes, cards
    "☀-➿"  # miscellaneous symbols and dingbats
    "⬀-⯿"  # arrows and shapes
    "︀-️"  # variation selectors
    "\U0001f3fb-\U0001f3ff"  # skin-tone modifiers
    "‍"  # zero-width joiner
    "]+"
)

#: Zero-width and directional marks. Invisible to a person, tokens to a model,
#: and they arrive from copy-paste more often than anyone expects.
_INVISIBLE = re.compile("[​‌‎‏⁠﻿]")

#: Discord's own noise: a custom emoji is markup, not a word.
_CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")


def transform(raw: str | None) -> Cleaned:
    """Split the code out, clean what is left.

    Order matters and is the point of the module. Cleaning first would strip a
    `→` out of a stack trace and collapse the newlines of a curl.
    """
    if not raw:
        return Cleaned(text="")

    code: list[str] = []
    fenced: list[bool] = []

    def take(was_fenced: bool):
        def held(match: re.Match) -> str:
            body = match.group(1).strip()
            if not body:
                return " "
            code.append(body)
            fenced.append(was_fenced)
            # A placeholder no cleaning rule touches, restored below. Held out
            # rather than removed: the code is part of what was said.
            return f"\x00{len(code) - 1}\x00"

        return held

    held = _INLINE.sub(take(False), _FENCED.sub(take(True), raw))
    cleaned = _clean(held)

    def restore(match: re.Match) -> str:
        index = int(match.group(1))
        body = code[index]
        return f"\n```\n{body}\n```\n" if fenced[index] else f"`{body}`"

    text = re.sub(r"\x00(\d+)\x00", restore, cleaned)
    return Cleaned(text=text.strip(), code=tuple(code))


def _clean(text: str) -> str:
    """Strip what carries no meaning, and collapse what carries too little.

    Blank lines survive as a single break: a paragraph boundary is real
    structure, and a message flattened to one line reads as one thought when it
    was three.
    """
    text = _CUSTOM_EMOJI.sub(" ", text)
    text = _EMOJI.sub("", text)
    text = _INVISIBLE.sub("", text)
    # Spaces and tabs within a line, then runs of blank lines.
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def render_attachments(attachments: tuple[Attachment, ...]) -> str:
    """Attachments as a line of text, so they survive being stored.

    A message is one string everywhere below this — in the database, in the
    prompt, in the tone examples. An attachment that is not in that string is
    an attachment nothing downstream can know about, and today they were
    dropped at the provider and never mentioned again: someone posts the
    screenshot of the error and the system asks them what the error was.

    Named, not linked. A URL in a prompt is an invitation to fetch something,
    and nothing here is allowed to.
    """
    if not attachments:
        return ""
    listed = ", ".join(
        f"{a.filename} ({a.content_type})" if a.content_type else a.filename
        for a in attachments
    )
    return f"[attached: {listed}]"
