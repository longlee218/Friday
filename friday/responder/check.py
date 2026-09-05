"""The floor under the one message that goes out unread.

`auto_ask_for_details` sends a request for missing details straight to the
reporter, and `config.yaml` justified that with a sentence: what is being
asked never changes, only the wording does. Nothing enforced it. The
responder's input carries other people's channel messages, so the single path
with no human in it was also the one whose wording a model writes from
untrusted text and signs with the operator's name.

The rule is the one `friday/dag/prepare.py` already states for validation:
**code is the floor, and a model's contribution is accepted only where code
has nothing to object to.** These rules are deliberately blunt, because the
cost of being wrong is asymmetric — a false refusal sends a plainer question,
and a false acceptance sends the operator's colleagues something the operator
did not say.

Here rather than in the pool, which is the only caller today, because what
this enforces is exactly what `friday/responder/prompt.py` promises: technical
words survive, nothing is offered that was not asked. A rule that lives beside
the prompt it holds to is one somebody editing that prompt will see.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["rejected"]

#: Phrases that turn a question into an undertaking. Vietnamese and English,
#: because the voice is Vietnamese with English technical words in it.
#:
#: The incident this list is drawn from is in `Responder.draft`'s docstring:
#: "ok có correlationId rồi, để anh trace thử" — a promise to trace, made
#: because the model found a correlationId belonging to a different report.
#: It is short, has no links, and names the field, so every other rule here
#: would have passed it.
#:
#: False positives are expected and are cheap: "để anh hỏi lại team nhé" is an
#: ordinary sentence and will be refused, and the reporter gets the plain
#: template instead. That is the trade this file is for.
_PROMISES = (
    "để anh",
    "để em",
    "để tôi",
    "để mình",
    "anh sẽ",
    "em sẽ",
    "tôi sẽ",
    "mình sẽ",
    "i'll",
    "i will",
    "let me",
    "we'll",
    "we will",
)


#: A word the responder is told not to translate. `camelCase` catches
#: `correlationId` and anything shaped like it without a list to maintain; the
#: literals are the rest of what `friday/responder/prompt.py` names.
_KEPT = re.compile(
    r"\b([a-z]+[A-Z]\w*|curl|staging|production|deploy|merge|timeout)\b"
)

#: Everything from the first bracket on. Both question builders put their
#: *reason* there and nothing else — `_question_from_clarify` appends the
#: extractor's `because`, and `_question` puts a validation rule's message
#: inline — and it is context for the responder about why the question is
#: worth asking, not something the reporter has to be asked about. Reading
#: technical words out of it made the check demand that a draft recite the
#: reasoning: the plainest correct rewording of "which environment you're on
#: (must be one of: dev, production, staging)" was refused for not repeating
#: the enum.
#:
#: From the first bracket rather than a balanced pair, because `because` is
#: free text an extractor wrote and can contain a bracket of its own — and a
#: strip that stops at the first `)` leaves half the clause behind, which is
#: the same bug wearing a different input.
_REASON = re.compile(r"\(.*$", re.S)

#: Anything that looks like somewhere to go rather than something to say.
#: Matched against the folded text, not the raw draft: Discord linkifies
#: `HTTPS://` exactly as it does `https://`, so a case-sensitive test was a
#: one-character evasion that produced a real, clickable link in a message
#: sent under the operator's name.
_LINK = re.compile(r"https?://|\bwww\.")

#: How much longer than the question the answer may be. The template is one
#: sentence; a draft several times its length has done something other than
#: rephrase it, whatever the extra happens to say.
_LONGER_THAN = 4
#: And a floor, so a short template does not make every reasonable draft too
#: long. The operator's real messages run to about this.
_AT_LEAST = 240


def _fold(text: str) -> str:
    """One spelling and one case, so a rule is about words rather than bytes.

    Vietnamese has two Unicode spellings of every accented letter and they
    compare unequal: `để` written decomposed walked straight past the promise
    list, which no editor shows and no reader would see. Normalising first is
    what makes a word list a list of words.
    """
    return unicodedata.normalize("NFC", text).casefold()


def rejected(draft: str, *, asking: str) -> str | None:
    """Why this draft may not go out unread, or `None` if it may.

    A reason rather than a bool, because the caller logs it: a fallback that
    happens silently hides a prompt regression, and the whole point of writing
    the draft was that somebody thought the wording mattered.
    """
    said = _fold(draft)
    for promise in _PROMISES:
        if promise in said:
            return f"it promises something ({promise!r})"

    kept = {m.group(0) for m in _KEPT.finditer(_REASON.sub("", asking))}
    if kept and not any(_fold(word) in said for word in kept):
        # One of them, not all of them. The field has a name in the reporter's
        # logs and that name is what makes the question answerable, so a draft
        # that translates every one of them away asks something nobody can act
        # on — but the template often offers a choice ("the correlationId, or
        # the curl you used"), and answering half of it is the point of asking.
        # Demanding all of them refused drafts that had done nothing wrong.
        return f"it no longer names any of {', '.join(sorted(kept))}"

    if _LINK.search(said):
        return "it carries a link, and a question for missing details needs none"
    if "```" in draft:
        return "it carries a code block, which a question does not need"

    bound = max(_AT_LEAST, len(asking) * _LONGER_THAN)
    if len(draft) > bound:
        return f"it is {len(draft)} characters long, over {bound}"
    return None
