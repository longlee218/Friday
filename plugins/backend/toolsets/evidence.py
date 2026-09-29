"""What a run has read, numbered once: the grounding index every backend tool
writes into (`RunContext.evidence`).

Moved out of `plugins/backend/investigate.py` when `sources/` folded into
`toolsets/` (build-the-spine ticket 09): the read tools now live beside the
client each one reads through (`logs.py`, `code.py`, `docs.py`, `db.py`), and
this is the one piece they share.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

__all__ = ["MAX_READS", "Evidence"]

#: How many reads one investigation may make. Not a cost control — that is
#: the agent's turns and tokens: a run that has looked twelve times and not
#: found it is a run that should say so.
MAX_READS = 12


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
    #: The running tag learnt per repo, `(tag, why not)` — kept here, the one
    #: per-run object every toolset shares, so `backend.code` and
    #: `backend.docs` ask `release_status` once between them.
    tags: dict[str, tuple[str, str]] = field(default_factory=dict)

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

    def spent(self) -> str:
        """`""` while there is budget, else the sentence that says there is
        not. Checked by every tool, so the ceiling cannot be forgotten in one
        of them."""
        if self.reads < MAX_READS:
            return ""
        return (
            f"You have made {MAX_READS} reads, which is the limit for one "
            f"investigation. Answer with what you have, and say in "
            f"`next_checks` what you would have looked at next."
        )
