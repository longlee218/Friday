"""Whether a line offered as a memory reads as an instruction rather than a
fact (board `what-the-room-already-knows`, ticket 11, D25).

A memory is read back as a statement of fact by a run that has none of the
context that produced it, so a line that tells *this system* what to do —
send without approval, always reply in English, skip the validation — is an
instruction with a long life and no author present. This is checked
deterministically, with no model, at the single write path every producer in
D19 goes through: `Database.memory_add`/`memory_update`/`memory_supersede`
all call `check_not_instruction_shaped` before a line becomes a row, so a
producer cannot bypass it by finding a different way in. (A channel file's
`set_overrides`/`init_channel` were two more doors until the YAML files
went — board `read-it-the-way-the-operator-does`, ticket 10 — and the
operator's hand writes rows through the same one now.)

**Narrower than "any imperative sentence".** `never deploy on fridays` is a
domain constraint about the team's own practice, and `MemoryKind.CONSTRAINT`
exists to hold exactly that shape — "what must not happen here, and what
always has to" (`friday/memory/channel_context.py`'s own summary job).
Refusing every directive-shaped line would refuse the one kind meant to carry
them. What is refused instead is a directive aimed at *this system's own
mechanism* — sending, replying, approving, validating, escalating — not one
describing the world the reporters work in. A line only trips this guard when
it is shaped like a command (leads with a bare verb, or with `always`,
`never`, `don't` and the like) *and* names one of this system's own moving
parts; either alone is not enough, which is what keeps `never deploy on
fridays` and `must include the X-Request-Id header` on the accepted side.
"""

from __future__ import annotations

import re

__all__ = ["InstructionShaped", "check_not_instruction_shaped"]


class InstructionShaped(ValueError):
    """Raised instead of writing a line shaped like a directive at this
    system's own mechanism. The message is what the producer is told."""


#: A sentence-initial word this shaped is characteristically imperative
#: mood, not a declarative fact about the world — a fact is stated about a
#: subject ("the queue…", "test.apero…"), never led by one of these.
_DIRECTIVE_LEADS = {"always", "never", "don't", "must", "should", "please"}

#: Bare-verb leads: an imperative has no subject, so the sentence opens on
#: the verb itself. Scoped to actions *this system's* own mechanism can take
#: — not a general English verb list, which would also catch a legitimate
#: instruction about the reporter's own system ("restart the service"). Some
#: of these also appear in `_MECHANISM_WORDS` below — that overlap is fine,
#: because the two are checked over disjoint spans of the sentence (this one
#: only the lead word, the other only what follows it) rather than over the
#: same text, so a word being in both never lets one condition stand in for
#: the other.
_IMPERATIVE_VERBS = {
    "send", "reply", "respond", "answer", "skip", "ignore", "disregard",
    "approve", "refuse", "escalate", "override", "bypass", "pretend", "act",
    "treat", "validate",
}

#: This system's own mechanism, named — the half of the rule that keeps
#: `never deploy on fridays` (a domain constraint) out of this net while
#: still catching `always reply in English` (a directive at the agent).
#: Matched as whole words only (`_MECHANISM_PATTERN`), and only against the
#: sentence *after* its lead — "send the invoice every month" must not be
#: refused just because `send`, its own first word, also happens to be a
#: mechanism word.
_MECHANISM_WORDS = (
    "approval", "approve", "approved", "validate", "validation", "review",
    "reviewed", "confidence", "escalate", "escalation", "hand over",
    "handover", "refuse", "refusal", "ignore", "disregard", "override",
    "bypass", "instruction", "instructions", "prompt", "rule", "rules",
    "persona", "roleplay", "pretend", "reply", "respond", "response",
    "send", "message", "language", "english", "vietnamese", "tone", "voice",
)

#: Whole-word (or whole-phrase) matches only. A raw substring test once let
#: `invoice` trip on `voice` and `resend` trip on `send` — found by review,
#: not written correctly the first time.
_MECHANISM_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _MECHANISM_WORDS) + r")\b"
)


def _lead_word_count(text: str) -> int:
    """How many words at the front of `text` make it read as a directive —
    `0` if it does not. `do not X` consumes two; every other lead consumes
    one, which is where `_MECHANISM_PATTERN` starts looking."""
    words = text.strip().split()
    if not words:
        return 0
    first = words[0].strip(".,!?;:\"'").lower()
    if first == "do" and len(words) > 1 and words[1].strip(".,!?;:\"'").lower() == "not":
        return 2
    if first in _DIRECTIVE_LEADS or first in _IMPERATIVE_VERBS:
        return 1
    return 0


def check_not_instruction_shaped(text: object) -> None:
    """Raise `InstructionShaped` if `text` reads as a directive at this
    system's own mechanism rather than a fact about the world.

    Non-`str` values (a nested mapping, a structured kind's `data`) are not
    this check's business and always pass: a directive lives in a line of
    prose, not in a structure.
    """
    if not isinstance(text, str):
        return
    words = text.strip().split()
    lead = _lead_word_count(text)
    if lead == 0:
        return
    remainder = " ".join(words[lead:])
    if _MECHANISM_PATTERN.search(remainder.lower()):
        raise InstructionShaped(
            "that reads as an instruction, not a fact — a memory is read "
            "back as a statement by a run with none of this context, so "
            "phrase it as one instead"
        )
