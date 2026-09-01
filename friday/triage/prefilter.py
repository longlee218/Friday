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
        self._patterns = tuple(
            (word, re.compile(rf"(?<![\w-]){_fold(word)}(?![\w-])"))
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
        folded = _fold(text)
        for word, pattern in self._patterns:
            if pattern.search(folded):
                return word
        return None


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
