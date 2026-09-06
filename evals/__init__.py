"""The regression net for the classifier — `docs/DESIGN.md`'s own words for
what this was always supposed to be. Not exercised by `uv run pytest`: it
calls the configured provider, so it costs money and is scored by hand, when
a prompt changes, against the numbers `evals/README.md` says to expect.

Two pure modules — `scoring.py`, `dataset.py` — hold everything a test can
check without a network. `build_triage_set.py` and `run_triage_eval.py` are
scripts, not tests: the first refreshes `triage.jsonl` from live data, by
hand, when there is new data worth freezing in; the second scores the live
classifier against whatever `triage.jsonl` currently holds.
"""

from __future__ import annotations
