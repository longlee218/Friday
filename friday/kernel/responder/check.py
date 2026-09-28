"""The floor under the one message that goes out unread.

`auto_ask_for_details` sends a request for missing details straight to the
reporter, and `config.yaml` justified that with a sentence: what is being
asked never changes, only the wording does. Nothing enforced it. The
responder's input carries other people's channel messages, so the single path
with no human in it was also the one whose wording a model writes from
untrusted text and signs with the operator's name.

The rule is the one `friday/kernel/dag/prepare.py` already states for validation:
**code is the floor, and a model's contribution is accepted only where code
has nothing to object to.** These rules are deliberately blunt, because the
cost of being wrong is asymmetric — a false refusal sends a plainer question,
and a false acceptance sends the operator's colleagues something the operator
did not say.

Here rather than in the pool, which is the only caller today, because what
this enforces is exactly what `friday/kernel/responder/prompt.py` promises: technical
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
#: **It is no longer the rule carrying this, and ticket 12 is why.** Drawn from
#: one sentence, it caught that sentence: every `ask_for_details` this system
#: has ever sent — three of them, on 2026-09-14, eight days after this list
#: shipped — ended in "anh trace giúp", which is the same promise in a
#: spelling with no entry here. A blacklist of phrasings cannot close a
#: language; `_WORK` below is the rule in the other direction. This stays
#: because it catches an undertaking with no named work in it ("để anh lo",
#: "I'll"), which `_WORK` does not.
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


#: Work this system might appear to be undertaking. Vietnamese and English,
#: and shared where the word is shared — `trace`, `check` and `deploy` are
#: written in English in this room whatever language the sentence is in.
#:
#: **This is `_KEPT`'s rule read backwards, and that is the whole idea.**
#: `_KEPT` says the draft must still name what the template named. Every
#: question this system can ask is for a *thing* — "the correlationId, or the
#: curl you used", "which environment you're on", "what access you need" — and
#: not one of them names an action. So an action named in the draft is content
#: the model added, and adding content is exactly what `config.yaml`'s reason
#: for skipping approval says cannot happen: what is being asked never
#: changes, only the wording does.
#:
#: It therefore refuses more than promises, on purpose. "em check trong
#: Postman tab Headers là thấy" undertakes nothing and is still a model
#: teaching the reporter something nobody approved, written from channel text.
#:
#: `xem` and `lo` were left out of the first version of this list as "too
#: ordinary to carry the meaning", and review put them back: the ticket's own
#: evidence that a phrasing list cannot close a language was six spellings of
#: one promise, and `a lo` and `anh xem cho` were two of the six. Leaving out
#: the examples the argument rests on is optimising the cheap side of a trade
#: this file states the other way round.
#:
#: Deliberately still *not* here: `làm`, which is every second sentence in
#: this language ("em làm gì", "anh làm ơn"), and `test`, a noun in this room
#: ("môi trường test") before it is ever a verb. Both would refuse far more
#: than they caught. What that leaves open is in the ticket, not hidden here.
_WORK = (
    "trace",
    "check",
    "kiểm tra",
    "debug",
    "fix",
    "sửa",
    "deploy",
    "restart",
    "xử lý",
    "điều tra",
    "rà soát",
    "investigate",
    "look into",
    "handle",
    "xem",
    "lo",
)

#: English inflection, because `trace` and `tracing` are one verb and a rule
#: that knows only the first is one a model walks past by conjugating —
#: review's sharpest finding, and it had written `anh tracing giúp` straight
#: through. A trailing `e` is dropped first, so `trace` reaches `tracing` and
#: `handle` reaches `handling`.
#:
#: Tried on the ASCII entries because that is where English inflection lives.
#: `xem` and `lo` are Vietnamese and ASCII and pick up a suffix group that
#: matches nothing anyone writes, which is noise rather than a bug. Doubled
#: consonants are missed — `debugging` is not `debuging` — and that is a known
#: hole, not a claim.
_INFLECTION = r"(?:e|es|ed|s|ing)?"


#: A word the responder is told not to translate. `camelCase` catches
#: `correlationId` and anything shaped like it without a list to maintain; the
#: literals are the rest of what `friday/kernel/responder/prompt.py` names.
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


def _says(folded: str, word: str) -> bool:
    """Whether `folded` names `word` as a word rather than as a run of letters.

    `checklist` is not `check`, and a rule that cannot tell them apart refuses
    a draft for containing a longer word that happens to start the same way.
    Built here rather than as a precompiled alternation so the list above
    stays a list of words somebody can add to without writing a pattern.

    A multi-word entry matches across any run of whitespace, because
    `re.escape` turns the space in `kiểm tra` into a literal one and a model
    that wrote `kiểm  tra` or broke the line between them would otherwise walk
    past. Same reason everything here is folded first: a rule about words has
    to be about words, not about the bytes somebody happened to type.
    """
    pattern = re.escape(word).replace(r"\ ", r"\s+")
    if word.isascii():
        pattern = pattern.removesuffix("e") + _INFLECTION
    return re.search(rf"\b{pattern}\b", folded) is not None


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

    # After `_PROMISES` rather than before it, so the sentence that incident
    # is recorded under still comes back named as a promise. Both are the one
    # concern — a draft doing something rather than asking something — and
    # they read better together than apart.
    #
    # **Against the question with its reason clause stripped**, which is the
    # same `_REASON` the rule below already applies and for a sharper reason
    # than that one has. The exemption here says: if the *question* named the
    # work, a draft repeating it is wording. The reason clause is not the
    # question — `_question_from_clarify` appends `clarify.because`, free text
    # an extractor model wrote from channel content — so without the strip a
    # model could switch this rule off for its own draft by writing `trace`
    # into `because`. That is the exact shape this file exists to close, one
    # level up: the one path with no human in it, with its floor removable by
    # the untrusted half. Found in review, measured, not reasoned about.
    #
    # Everything the strip leaves is code: `asked_as` phrases from
    # `friday/sdk/validation.py` and `_RULES` messages from
    # the same module, and both builders put every model-written
    # word inside the bracket.
    asked = _fold(_REASON.sub("", asking))
    added = [w for w in _WORK if not _says(asked, w) and _says(said, w)]
    if added:
        return f"it names work the question did not ({', '.join(added)})"

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
