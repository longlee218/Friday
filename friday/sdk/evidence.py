"""What a run has read, numbered once: the grounding index every read tool
writes into (`RunContext.evidence`).

In the sdk since build-the-spine ticket 14: the spine builds one per agent
run and stores it with an `Ask` (a continuation point), so the core names it;
backend's toolsets and core `shell` write into it. It was
`plugins/backend/toolsets/evidence.py` (ticket 09) and, before that,
`plugins/backend/investigate.py`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Evidence"]


@dataclass
class Evidence:
    """Every line any tool has shown this run, under the id it was shown as.

    **The grounding gate lives or dies here.** `refs` are line ids and an id
    naming nothing voids the answer — measured, because asked to quote a JSON
    log line the configured model succeeded 32 times in 40 and asked to point
    at one it succeeded 20 in 20. With a fixed pipeline the index was built
    once, from a dossier and a code excerpt. With tools it has to
    **accumulate**: a line shown by the fourth call must be as citable as one
    shown by the first, and ids must never be reused for different text.

    Numbering continues across calls for exactly that reason. `L7` means one
    line for the life of a run, whichever tool produced it.
    """

    index: dict[str, str] = field(default_factory=dict)
    _seen: dict[str, str] = field(default_factory=dict, repr=False)
    #: What was read but not shown — a capped window, a file that is not in
    #: the clone. Merged into the node's own `not_checked`, so the honest
    #: half survives the model choosing what to look at.
    not_checked: list[str] = field(default_factory=list)
    reads: int = 0

    def show(self, lines: Iterable[str]) -> str:
        """Number these lines, continuing this run's numbering.

        Blank lines keep their place and take no id: an id that points at
        nothing is an id a model can cite to mean anything.
        """
        rendered = []
        for raw in lines:
            if not str(raw).strip():
                rendered.append("")
                continue
            text = str(raw)
            # **One id per distinct line, reused.** Windows overlap — a model
            # that widens its search sees what it already saw — and issuing a
            # second id for the same text pays for it twice and offers two
            # ways to cite one thing.
            ref = self._seen.get(text) or f"L{len(self.index) + 1}"
            self._seen[text] = ref
            self.index[ref] = text
            rendered.append(f"{ref} | {raw}")
        return "\n".join(rendered)

    def dump(self) -> dict[str, Any]:
        """As JSON data, for a stored `Ask`: `load` gives back an `Evidence`
        that numbers on from where this one stopped."""
        return {
            "index": dict(self.index),
            "seen": dict(self._seen),
            "not_checked": list(self.not_checked),
            "reads": self.reads,
        }

    @classmethod
    def load(cls, data: Mapping[str, Any]) -> Evidence:
        return cls(
            index=dict(data["index"]),
            _seen=dict(data["seen"]),
            not_checked=list(data["not_checked"]),
            reads=int(data["reads"]),
        )
