"""What never reaches the model.

Some messages must not be sent to a third-party API to be told what they are.
Pay between colleagues, a medical record, a password, someone asking for an API
key — the harm is in the sending, so the decision has to be made *before* the
call, by a rule that a persuasive message cannot argue with. That is why this
is a word list and not a judgement.

**Held, never dropped.** A message that trips this becomes work for the
operator, not silence. That matters more here than it looks: the list contains
words that appear in ordinary reports — "API trả 401, token hết hạn rồi" is a
bug, "cho em xin api key của staging" is an access request — and a rule that
skipped them would be dropping real mentions on the strength of one word. The
guarantee is *the model does not see it*, not *nobody sees it*.

The words are configuration, not code. The operator adds to this list as they
notice things, and that should be a line in `config.yaml` and a restart, not a
commit.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

__all__ = ["Sensitive"]


class Sensitive:
    """The words that keep a message away from the model.

    Matched on whole words, case-insensitively, and with Vietnamese diacritics
    folded away — so `lương` in the list also catches `luong`, which is how
    half of Vietnamese chat is typed. A phrase like `xin token` matches only
    when its words appear together and in order.
    """

    def __init__(self, words: Iterable[str]) -> None:
        self._words = tuple(w for w in (w.strip() for w in words) if w)
        # Two patterns per word, run against two spellings of the message.
        # Folding alone made `luồng` (a flow) the same word as `lương`
        # (salary) and `thường` (usual) the same as `thưởng` (bonus) — both
        # everyday words in a backend channel, both silently held from the
        # model. What folding is *for* is the message typed without
        # diacritics, so that is the only place it is allowed to decide.
        self._patterns = tuple(
            (
                word,
                re.compile(rf"(?<![\w-]){re.escape(_lower(word))}(?![\w-])"),
                re.compile(rf"(?<![\w-]){re.escape(_fold(word))}(?![\w-])"),
            )
            for word in self._words
        )

    def __len__(self) -> int:
        return len(self._words)

    def found(self, text: str) -> str | None:
        """The first configured word this message contains, or None.

        The word is returned rather than a bare `True` so the operator can be
        told *why* their message was held. The message itself is never part of
        that explanation — it is the thing being kept quiet.
        """
        lowered = _lower(text)
        folded, accented = _fold_marked(lowered)
        for word, exact, folded_pattern in self._patterns:
            if exact.search(lowered):
                return word
            # The folded spelling decides only where the message wrote no
            # diacritics of its own. `luồng` must not match `lương` because
            # both fold to `luong`; `luong` still must.
            for hit in folded_pattern.finditer(folded):
                if not any(accented[hit.start() : hit.end()]):
                    return word
        return None


def _lower(text: str) -> str:
    """Lowercase and collapse spaces, diacritics kept.

    The message's own spelling, for the exact half of the match.
    """
    return re.sub(r"\s+", " ", text.lower().replace("đ", "d"))


def _fold_marked(lowered: str) -> tuple[str, list[bool]]:
    """The folded spelling, and one flag per folded character saying whether
    it carried a diacritic in the message.

    Character by character rather than over the whole string, because a
    message mixes the two spellings freely — "luồng thanh toan" is both — and
    the question is about the word that matched, not about the message.
    """
    out: list[str] = []
    marked: list[bool] = []
    for ch in lowered:
        decomposed = unicodedata.normalize("NFD", ch)
        base = "".join(c for c in decomposed if not unicodedata.combining(c))
        has_mark = len(decomposed) > len(base)
        for c in base or ch:
            out.append(c)
            marked.append(has_mark)
    return "".join(out), marked


def _fold(text: str) -> str:
    """Lowercase, strip diacritics, and collapse the spaces between words.

    `Lương` and `luong` are the same word to anyone reading, and a list that
    needed both spellings for every entry would be a list nobody maintains.
    `đ` is spelled out because it is a distinct letter, not an accented `d`,
    and NFD leaves it alone.
    """
    lowered = text.lower().replace("đ", "d")
    stripped = "".join(
        ch
        for ch in unicodedata.normalize("NFD", lowered)
        if not unicodedata.combining(ch)
    )
    return re.sub(r"\s+", " ", stripped)
