"""Growing `evals/datasets/triage/` from what the operator marked ✅.

Run by hand (`run_eval.py core.triage --add-confirmed`), never by a scoring
run and never by the suite: a set that re-read the database on every run
would move under the prompt it is scoring. **It only adds.** The folder is
the operator's — hand-written turns, pasted tickets — so a refresh never
rewrites or deletes a case; a verdict whose text is already a case, or is one
of the examples the prompt itself shows the model, is left out.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from friday.kernel.evals.cases import load_turn_cases, write_turn_case
from friday.kernel.evals.triage import DATASET, decisions, unfit

if TYPE_CHECKING:
    from friday.kernel.config import Config
    from friday.store.db import Database

__all__ = ["add_confirmed"]

log = logging.getLogger(__name__)


async def add_confirmed(
    db: Database,
    config: Config,
    *,
    root: Path = DATASET,
    #: What must not be scored: the examples the model is shown. `None` reads
    #: them from the registered actions; a test names its own.
    excluded: list[tuple[str, str]] | None = None,
) -> list[Path]:
    """Write a case for each marked verdict not yet in the set, and return
    the files written. Warns, and still writes, when the set is not fit to
    score against — an operator mid-refresh needs the files to look at."""
    from friday.kernel.plugin_host import registered_actions
    from friday.kernel.triage.prompt import declared_examples

    actions = registered_actions(config)
    labels = decisions(actions)
    shown = {
        t.strip()
        for t, _ in (declared_examples(actions) if excluded is None else excluded)
    }
    have = {
        "\n".join(t for t, _ in case.inputs).strip() for case in load_turn_cases(root)
    }
    confirmed = await db.confirmed_classifications(limit=1000, decisions=labels)

    written = []
    for text, label in confirmed:
        if text.strip() in shown or text.strip() in have:
            continue
        written.append(write_turn_case(root, label, text))
        have.add(text.strip())
    log.info("%d case(s) added from marked verdicts", len(written))
    for problem in unfit(load_turn_cases(root), labels):
        log.warning("this set is not fit to score a classifier against: %s", problem)
    return written
